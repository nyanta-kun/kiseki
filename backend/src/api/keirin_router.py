"""競輪 picks/summary API ルーター

keirin スキーマ（PostgreSQL）を参照して結果を返す。

GET /api/keirin/picks?date=YYYY-MM-DD   - 指定日の推奨ピック一覧
GET /api/keirin/summary                  - 当日/当月/当年の投資・回収サマリー
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from datetime import date as Date
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.keirin_models import KeirinNetkeirinSetting
from ..db.session import AsyncSessionLocal, get_db
from ..services.keirin_crash_risk import race_risk, risk_band
from ..services.keirin_cup_grade import grade_label
from ..services.keirin_marquee import is_marquee_race
from ..services.keirin_race_confidence import (
    confidence_from_entries,
    confidence_hit_count_from_entries,
)
from ..services.keirin_result_top3 import winning_combo_labels
from ..services.keirin_sales_analysis import (
    ORIGIN_RANK,
    build_correlations,
    build_daily,
    build_leadtime_buckets,
    build_link_check,
    build_origin_breakdown,
    build_races,
    build_rank_breakdown,
    build_route_breakdown,
    build_summary,
)
from ..services.keirin_sales_report import REVENUE_RATE
from ..services.keirin_settlement import Settlement, payout_per_100, settle
from ..services.keirin_skip_reasons import (
    describe as skip_reason_describe,
)
from ..services.keirin_skip_reasons import (
    label as skip_reason_label,
)
from ..services.keirin_submission_window import SUBMIT_DEADLINE_SEC, is_closed
from .import_router import ApiKeyDep
from .keirin_meeting import first_hour_jst, meeting_type_of_first_hour


def _parse_bet_detail(raw: str | None) -> dict[str, Any] | None:
    """`keirin.netkeirin_submissions.bet_detail` を読む。

    生成元は keirin `scripts/netkeirin_submit_wt.py::build_bet_detail`。形式:
        {"total": 10000, "source": "blend",
         "lines": [{"bet_type": "3連複", "combo": "1=2=5", "stake": 4100}, ...]}

    ⚠️ **壊れていたら None を返して黙って落とす。** ここは表示のおまけであって、
       レース一覧そのものを 500 にしてよい情報ではない。
    """
    if not raw:
        return None
    try:
        d = json.loads(raw)
        lines = [
            {"bet_type": str(x["bet_type"]), "combo": str(x["combo"]),
             "stake": int(x["stake"]),
             # 入稿時点のオッズ。⚠️ 取れなかった場合は **null のまま**返す
             # （0 にすると表示側で「オッズ0倍」と読めてしまう）。
             "odds": (float(x["odds"]) if x.get("odds") else None),
             # "board"（実際に付いていた板）/ "predicted"（構造モデルの生成値）/
             # None（どちらも無い＝不明）。🔴 **表示で必ず区別する。**
             # 予測値を板と同じ顔で出すと「実際のオッズ」と読まれる。
             "odds_source": (str(x["odds_source"]) if x.get("odds_source") else None)}
            for x in d["lines"]
        ]
    except (ValueError, TypeError, KeyError):
        return None
    if not lines:
        return None
    return {"total": int(d.get("total") or sum(x["stake"] for x in lines)),
            "source": d.get("source"), "lines": lines}


_WEBHOOK_BASE = "http://172.18.0.1:8010"

_JST = timezone(timedelta(hours=9))


def _today_jst() -> Date:
    return datetime.now(_JST).date()

router = APIRouter(prefix="/api/keirin", tags=["keirin"])


# ---------------------------------------------------------------------------
# 合成オッズ計算
# ---------------------------------------------------------------------------

def _parse_combinations(pred_combo: str | None, is_wide: bool) -> tuple[list[list[str]], str | None]:
    """pred_combo 文字列を (買い目ごとのキー候補リスト, 券種) に変換する。

    wt_odds_snapshot の combination 表記は収集経路で混在するため
    （旧Mac収集: trio='1=2=6' / VPS収集: trio='1-2-3'）、順不同券種は
    両区切りのキー候補を返し、照合側でいずれか一致した方を使う。
    - 三連複（S1/S2/S3・'1-4-3,5,2'）→ [['1=3=4','1-3-4'], ...] / 'trio'（昇順）
    - 二連単（A・'1>3,4,5'）        → [['1-3'], ['1-4'], ['1-5']] / 'exacta'（着順どおり）
    - WIDE（'4-2'）                 → [['2-4','2=4']] / 'quinella'（昇順）
    ※ 旧実装は三連複の買い目に trifecta（三連単・1順序のみ）のオッズを使っており
      合成オッズを過大表示していた（2026-07-16 修正）。
    """
    if not pred_combo:
        return [], None
    try:
        if ">" in pred_combo:  # A（二連単）: "軸>相手1,相手2,..."
            axis, rest = pred_combo.split(">", 1)
            partners = [p.strip() for p in rest.split(",")
                        if p.strip() and p.strip().isdigit()]
            return [[f"{int(axis)}-{p}"] for p in partners], "exacta"
        parts = pred_combo.split("-")
        if is_wide and len(parts) == 2:
            a, b = sorted([parts[0].strip(), parts[1].strip()], key=int)
            return [[f"{a}-{b}", f"{a}={b}"]], "quinella"
        if len(parts) >= 3:
            a1, a2 = parts[0].strip(), parts[1].strip()
            thirds = [t.strip() for t in parts[2].split(",") if t.strip()]
            legs = []
            for t in thirds:
                s = sorted([a1, a2, t], key=int)
                legs.append(["=".join(s), "-".join(s)])
            return legs, "trio"
        if len(parts) == 2:
            a, b = sorted([parts[0].strip(), parts[1].strip()], key=int)
            return [[f"{a}-{b}", f"{a}={b}"]], "quinella"
    except (ValueError, TypeError):
        return [], None
    return [], None


async def _calc_synth_odds(
    db: AsyncSession,
    race_key: str,
    pred_combo: str | None,
    is_wide: bool,
) -> float | None:
    """直近スナップショットのオッズから合成オッズ（= 1 / Σ(1/odds)）を計算して返す。データ不足時は None。

    wt_odds_snapshot は当日 morning(8時台)〜h20(20時台) まで複数回収集される。
    朝の時点では大半の組み合わせが Winticket 側の未確定プレースホルダ(9999.9倍)の
    ままであり、snapshot_type を 'morning' に固定すると意味のない値になりやすい
    （例: 全4点が9999.9のまま→合成2500.0倍という無情報値。2026-07-20 発覚）。
    そのレース・券種で収集済みの最新スナップショットを使う。
    """
    legs, bet_type = _parse_combinations(pred_combo, is_wide)
    if not legs or bet_type is None:
        return None
    combos = [k for leg in legs for k in leg]
    rows = (await db.execute(
        text("""
            SELECT combination, odds_value
            FROM keirin.wt_odds_snapshot
            WHERE race_key = :rk
              AND bet_type = :bt
              AND combination = ANY(:combos)
              AND snapshot_at = (
                SELECT MAX(snapshot_at) FROM keirin.wt_odds_snapshot
                WHERE race_key = :rk AND bet_type = :bt
              )
        """),
        {"rk": race_key, "bt": bet_type, "combos": combos},
    )).mappings().all()

    odds_map = {r["combination"]: r["odds_value"] for r in rows if r["odds_value"]}
    # 買い目ごとにキー候補（=区切り/-区切り）のうち存在する方を1つだけ採用（二重計上防止）
    matched = []
    for leg in legs:
        for key in leg:
            if key in odds_map:
                matched.append(odds_map[key])
                break
    if not matched:
        return None

    return round(1.0 / sum(1.0 / o for o in matched), 2)


async def _calc_synth_odds_from_lines(
    db: AsyncSession, race_key: str, lines: Sequence[Mapping[str, Any]],
) -> float | None:
    """**入稿した買い目そのもの**の合成オッズ（= 1 / Σ(1/odds)）。

    `_calc_synth_odds` は `picks_history.pred_combo`（ランクの候補）用。売った商品は
    候補と買い目が違うことがある（看板の穴埋めで軸を組み替える等）ので、
    合成オッズも売ったほうから出さないと**買い目と数字がちぐはぐになる**。

    ⚠️ `wt_odds_snapshot.combination` は収集経路で表記が混在する
       （trio が `1=2=6` の回と `1-2-6` の回がある）ので、三連複は両方を候補にする。
    """
    by_type: dict[str, list[list[str]]] = {}
    for x in lines:
        kind = str(x.get("bet_type") or "")
        if kind == "3連複":
            bt, ordered = "trio", False
        elif kind == "3連単":
            bt, ordered = "trifecta", True
        else:
            return None                      # 未知の券種は黙って混ぜない
        try:
            cars = [int(c) for c in re.split(r"[-=]", str(x.get("combo"))) if c != ""]
        except ValueError:
            return None
        if len(cars) != 3:
            return None
        if ordered:
            by_type.setdefault(bt, []).append(["-".join(map(str, cars))])
        else:
            joined = sorted(cars)
            by_type.setdefault(bt, []).append(
                ["-".join(map(str, joined)), "=".join(map(str, joined))])
    matched: list[float] = []
    for bt, legs in by_type.items():
        combos = [k for leg in legs for k in leg]
        rows = (await db.execute(
            text("""
                SELECT combination, odds_value
                FROM keirin.wt_odds_snapshot
                WHERE race_key = :rk
                  AND bet_type = :bt
                  AND combination = ANY(:combos)
                  AND snapshot_at = (
                    SELECT MAX(snapshot_at) FROM keirin.wt_odds_snapshot
                    WHERE race_key = :rk AND bet_type = :bt
                  )
            """), {"rk": race_key, "bt": bt, "combos": combos},
        )).mappings().all()
        odds_map = {x["combination"]: x["odds_value"] for x in rows if x["odds_value"]}
        for leg in legs:
            for key in leg:                  # 二重計上を避けて1点につき1つだけ採る
                if key in odds_map:
                    matched.append(float(odds_map[key]))
                    break
    if not matched:
        return None
    return round(1.0 / sum(1.0 / o for o in matched), 2)


# ---------------------------------------------------------------------------
# picks
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 集計対象ランクの単一正本（2026-08-01 是正）
#
# 【障害の経緯】keirin リポジトリ（別リポジトリ・commit f31f84b, 2026-07-31）が
# ランク体系を全面改名した（内部rank名を "RANK_" + 表示ラベル方式へ統一。
# 旧 SEVEN_S7→RANK_7S・SEVEN_7A→RANK_7A・NINE_S9→RANK_9S・NINE_9A→RANK_9A。
# 表示ラベル自体（7S/7A/9S/9A）は変更なし）。kiseki 側は旧rank名でDBを検索し
# 続けていたため picks_history の新データ（RANK_7S 等）を一切拾えず、Web表示が
# 「データがありません」になっていた（2026-08-01発覚・本セクションで是正）。
#
# 同時に、2026-07-31 keirin側で新設された RANK_7SS（波乱軸選出・穴レース検知。
# race_point/WINTICKET公式印/ライン構成のみで判定するモデル非依存の独立戦略。
# 旧「SEVEN_S7 かつ gate_label='SS'」だった7SS/9SSとは無関係の別物）をここで
# 初めて kiseki 側に追加した（ユーザー要望「7SS の追加を行ったため VPS の
# 7SS 表示も有効にして下さい」への対応）。
#
# また、旧実装の _display_rank() は SEVEN_S7/NINE_S9 を gate_label('SS'/'S') で
# 7SS/9SS・7S/9S に分岐していたが、この分岐は keirin側 commit e994758
# （2026-07-31）で廃止済み（rank_7s_gate_label() は常に "S" のみを返す。
# 既存行の gate_label も 'S' へ一括更新済み）。kiseki側もこれに追随し、
# gate_label による表示分岐を完全に廃止して rank→表示ラベルの単純な1対1
# マッピングへ変更した（gate_label カラム自体は過去データ分析用に DB からは
# 削除しない）。
#
# 【単一正本】keirin側 src/strategy_wt.py の CURRENT_PAPER_RANKS が正本
# （keirin は別 venv（FastAPI も SQLAlchemy も無い）で動くうえ `src` パッケージ名が
#  衝突するため import はできない。以下の辞書へ手動で複製する）。
# 🔴 **複製のずれは `backend/tests/test_keirin_rank_consistency.py` が
#    keirin 側の CURRENT_PAPER_RANKS をパースして機械照合している。**
#    2026-08-12 に 7H3 を新設した際、フロント7箇所を守る既存テストは
#    「正本に無いものは要求されない」ため**空振り**し、
#    設定画面に 7H3 が出ないまま気づけなかった。上流との照合を足して塞いだ。
# _VALID_PICK_RANKS / _RANKS_ALL / _display_rank() は全てこの辞書から導出し、
# ランク名の二重管理を避ける（新ランクの追加/廃止時は _PAPER_RANK_LABELS の
# みを更新すればよい設計とする。同じ名前を複数箇所にハードコードし直す運用が
# 今回の障害の一因だったため）。
#
# 全廃済み（picks_history に残っていても表示・集計対象から除外する残骸）:
#   SEVEN_S1（win軸1着固定×3着内モデル相手2車・三連単2点流し。2026-07-31全廃）
#   SIX_S1 / 7PLUS_R / 7PLUS_U / 7PLUS_M / 7PLUS_ST / 7PLUS_STP（いずれも既に全廃済み）
# ---------------------------------------------------------------------------
# 定義順 = Web 全体の表示順（7S/7A/9S/9A）。
#
# 【2026-08-02】RANK_7SS（波乱軸選出・穴レース検知）を全廃した（ユーザー判断）。
# live実績が picks_history 全期間 n=16,298 で ROI 73.5%、2026年の月次も
# 94.4/61.0/56.3/61.1/69.3/70.2/60.3% と1月以外すべて控除率75%を大きく下回り、
# 有効な推奨として成立していなかったため。picks_history の RANK_7SS 行も
# 同日削除済み（退避: keirin repo data/backup/
# picks_history_rank_7ss_before_abolition_20260802.csv）。
# 将来「期待できる推奨条件」が見つかった場合はこの辞書へ1行戻せば
# Web表示・集計・netkeirin設定すべてが復活する（keirin側の候補生成停止も
# 併せて解除すること）。
#
# 【2026-08-05】RANK_7SS を新設（keirin PR#10 `cb419d4`）。上記で全廃した
# 旧 RANK_7SS（波乱軸選出・穴レース検知・モデル非依存）とは**無関係の別戦略**で、
# 名前だけを引き継いだもの。新定義は「7S のゲートのうち entropy だけ不合格
# （= 荒れ）∧ 軸1と軸2が同一ライン（wt_entries.line_group 一致）」で、買い目は
# 7S/7A と同じ三連複 軸2車+総流し5点。確認窓（2024-07〜2025-06・掃引未使用）で
# 1.90件/日・的中41.2%・ROI 85.9% と現行ランク中で最良のため最上位に置く。
# picks_history の旧7SS行は 2026-08-02 に全削除済み（0件）なので成績は混ざらない。
# 🔴 2026-08-14: 旧 7SS / 7A を RANK_7S へ統合した（ユーザー判断）。
# 3ランクは買い目構造が同一で、live 実績（n=7,461・32ヶ月）でも ROI・的中率・
# 払戻中央値・ガミ率が統計的に区別できなかった。選別は変えていないので
# 買うレースは1件も増減しない（keirin 側 `rank_7s_merged_daily_select`）。
# picks_history の旧 7SS/7A 行は全期間再構築で rank を RANK_7S へ付け替える。
_PAPER_RANK_LABELS: dict[str, str] = {
    # RANK_7H2: 2026-08-10 新設の**穴推奨**（印なし2軸・高配当）。7H1 と同じ7車立て
    #    なので**母集団は排他ではない**（重なりは 7H1 側の 49.2%）。7H1 が「本命が
    #    飛ぶ」と読んだレースを選ぶのに対し、7H2 は本命の生死を読まず
    #    **軸2車を WT公式印の付いていない車に限定する**ことで配当帯を移す。
    #    券種は 7H1 と同じ2券種だが、三連単は**倍購入10点**（軸2を2着・3着の両方に
    #    置く）で、単一のフォーメーションには畳めない。pred_combo は "三複:… / 三単:…"。
    # 手動入稿（_MANUAL_RANK_KEYS）は軸2車を選ぶUIのため**対象外**（7H1 と同じ理由）。
    "RANK_7H2": "7H2",
    # RANK_7T1: 2026-08-13 新設の**三連単・高配当枠**（旧 RANK_7H3 を置換）。
    #    軸1を1着・軸2を2着に固定し、3着だけを1〜5点に絞る三連単フォーメーション。
    #    点数と足切りは「1レース1万円を等分して払戻20万円に届くか」から導くので
    #    **レースごとに点数が変わる**（実測 平均2.0点）。
    #    母集団は **決勝系レース（決勝/準決勝/特選/選抜）∧ 3着内率の上位2車が
    #    別ライン** の7車立て。🔴 **「看板」ではない**（看板判定は準決勝を除外するが
    #    7T1 は含む）。看板+準決勝を対象とする的中率商品（7C 等）と**同じレースを
    #    取り合う**（入稿の優先順位で 7C が先に取る）。表示的中は約3%。
    # ⚠️ 三連単の単一券種。買い目文字列が "軸1-軸2-相手" の順序つきなので、
    #    合成オッズ（_parse_combinations）は 7H1 と同じく算出対象外になる。
    # 手動入稿（_MANUAL_RANK_KEYS）は軸2車を選ぶUIのため**対象外**。
    #
    # 🔴 旧 RANK_7H3（2026-08-12〜08-13・入稿実績0）はここから削除済み。
    #    picks_history の 7,090行も廃止時に削除した（退避CSVは keirin の
    #    data/backup/picks_history_rank_7h3_before_abolition_20260813.csv）。
    "RANK_7T1": "7T1",
    # RANK_7T3: 2026-08-24 新設の**三連単・決勝の中配当枠**。7T1 と違い
    #    **軸を固定しない**——予測オッズ30倍以上の目に限定し、位置別合成
    #    Plackett-Luce の確率上位5点を均等（2,000円/点）で買う。
    #    母集団は **決勝（完全一致・準決勝を含まない）** の7車立てで、
    #    **ライン条件を持たない**。入稿の優先順位で 7T1 の直後に置くため、
    #    7T1（決勝×別ライン）が取ったレースは降り、結果として同ラインを拾う。
    # ⚠️ 表示的中は約10%・払戻中央 7.8万円（1レース1万円）。
    #    🔴 **万車券枠ではない**（的中の100倍超は 4.7%・3万円超は0件）。
    # ⚠️ ◎○ は「1着に最も多く現れる車 / ◎を除き1-2着に最も多く現れる車」で、
    #    **買い目の軸ではない**（見解本文でも「二軸」と書かない）。
    #    設計: keirin/docs/rank_7t3_design.md
    # 手動入稿（_MANUAL_RANK_KEYS）は軸2車を選ぶUIのため**対象外**。
    "RANK_7T3": "7T3",
    "RANK_7S": "7S",
    # RANK_7B: 2026-08-03 新設。軸2車がWT公式印◎◯と完全一致するが、順序
    # （モデル1位≠◎）と相手（△を買い目から除外）で市場と不一致なレース。
    # 7S/7A が枯渇（overlap∈{0,1}が18〜23%まで低下）したことへの増枠。
    # 三連複の相手絞り3点で 7S/7A の5点総流しとは点数が異なる。
    "RANK_7B": "7B",
    # 🔴 2026-08-21: 入稿の優先順位で 7B を 7C の上へ移した
    #    （1.5倍以上の的中で +5〜6pt・ROI も上・両年独立で再現）。
    #    表示順は「車数＞入稿の優先順位」なのでここも追随する。
    # RANK_7C: 2026-08-07 新設の**ベースモデル**（終日の二軸）。既存6ランクと
    # 違い wt_overlap_n を見ないため**同一レースに併存しうる**（picks_history の
    # race_key は `{レースキー}#{suffix}` なので行は共存できる）。
    "RANK_7C": "7C",
    # 🔴 **2026-08-15 に入稿の優先順位を最下位へ落とした**（それ以前は先頭）。
    #    表示順は「車数＞入稿の優先順位」なので、ここの位置もそれに追随する。
    # RANK_7H1: 2026-08-06 新設の**穴推奨**（本命バスト型）。既存6ランクとは系統が
    # 違う（S/A/B＝的中率重視の予想ベース、H＝穴狙い）。命名は `{車数}H{連番}`。
    # 「当方指数で頭ひとつ抜けた1車が4着以下に沈む」とレース単位モデルが読んだ
    # 7車立てだけを選び、**その本命と同ラインを買い目から丸ごと落とす**。
    # 🔴 **2026-08-15 に三連単一本化**（ユーザー指示。それ以前は唯一の2券種ランクで
    #    三連単F8点 + 三連複BOX 4〜10点だった）。いまは 9H1 と同じ**三連単
    #    フォーメーションの単一券種**（8点）で、pred_combo は "三単:…"、
    #    払戻は payout / trifecta_payout に入り trio_payout は常に0。
    #    ⚠️ 過去分は全期間再構築で新ルールへ揃えてある（旧 "三複:… / 三単:…" の
    #    行は残っていない）。買い目文字列が順序つきなので、合成オッズ
    #    （_parse_combinations）は算出対象外（パース失敗で None を返す）。
    # 手動入稿（_MANUAL_RANK_KEYS）は軸2車を選ぶUIのため**対象外**。
    "RANK_7H1": "7H1",
    # RANK_7M1: 2026-08-17 新設の**中間層**（混戦 × 市場乖離）。系統文字 M = Middle。
    #    ベース層（7C/7S/9C・的中38〜47%・払戻中央1.5倍台）と穴推奨（7H1/7T1・
    #    的中3〜5%・中央8〜22倍）の**間の払戻帯**を埋めるために新設した。
    #    母集団は「3着内率の上位2車の合計 < 1.44（＝7C が取らない混戦）∧
    #    その2車が WT公式印の ◎○ と一致しない」7車立てで、買い目は三連複・
    #    軸2車から**相手を指数下位3車**（軸を除く5車の下位3＝全体で5〜7番手）を採り、
    #    そのうち3着内率0.15未満を削った2〜3点。相手の上位2枚（全体3・4番手）を
    #    あえて捨てて配当を作る（全体4番手は相手中で最も割高な1枚）。
    #    honest walk-forward 6,275R・本番と同じ予算枠+傾斜配分で
    #    的中11.3% / 払戻中央5.66倍 / ROI 82.3%。
    # 🔴 **入稿の優先順位は最下位**（7H1 の下）。7S とは当たり方が部分集合で、
    #    7H1 とは排他だが ROI は 7H1 が上。重なったら必ず譲る。
    # 🔴 **月次の ROI で採否を判断しないこと**（±2.5pt に約15.6年かかる層）。
    #    月次で読むのは 件数・的中率・払戻中央値の3つだけ。
    # 手動入稿（_MANUAL_RANK_KEYS）は軸2車を選ぶUIのため**対象外**。
    "RANK_7M1": "7M1",
    # RANK_9H1: 2026-08-08 新設の**穴推奨**（9車・高配当狙い）。7H1 と同じ穴推奨系だが
    #    **車数で母集団が完全に排他**（7H1=7車ちょうど / 9H1=9車ちょうど）。
    #    券種は三連単フォーメーションの**単一券種**（6点）で、pred_combo は "三単:…"。
    #    払戻は payout だけに入る（trio_payout は常に0）。7H1 も 2026-08-15 から同型。
    # 手動入稿（_MANUAL_RANK_KEYS）は軸2車を選ぶUIのため**対象外**（7H1 と同じ理由）。
    "RANK_9H1": "9H1",
    # RANK_9C: 2026-08-14 新設の**9車ベースモデル**（旧 9S/9A を置換）。
    #    旧 9A は二軸的中 26.4% で「素直に p3上位2車を採る」(40.7%) より
    #    14.3pt 低く、ゲートが逆効果だった。9C は確認窓で 50.8% を再現。
    "RANK_9C": "9C",
}

# 候補行（判定前・見送り含む生候補）。ペーパーランクの1つではないが、
# write_candidates_wt.py が現在も書き込んでおり表示・集計対象に含める必要がある
# 特殊値（朝時点で rank='7PLUS_CAND' として書き込まれ、発走前オッズ確定時に
# 上記いずれかのランクへ判定・上書きされる）。
_CANDIDATE_RANK = "7PLUS_CAND"

# picks 一覧 API（/keirin/picks）の allowlist。denylist方式（rank != 'GAMI'等）だと
# 全廃済みランクの残留行（2026-07-27発覚: 2026-07-21に全廃したはずの
# 7PLUS_U/7PLUS_M が27行アーカイブ未済のまま残り「非」バッジで表示され、
# サマリー集計とも齟齬が生じていた）を拾ってしまう。allowlist方式にすることで
# 将来同種の残留が発生しても自動的に非表示になる（サマリー側の_aggregate()と
# 対象ランクを揃えることでも齟齬を防ぐ）。
_VALID_PICK_RANKS = "(" + ", ".join(f"'{r}'" for r in (*_PAPER_RANK_LABELS, _CANDIDATE_RANK)) + ")"


def _finishers(entries: Iterable[Any]) -> list[tuple[int, int]]:
    """`(着順, 車番)` の並び（3着以内のみ）。

    🔴 **「ちょうど3件」に絞ってはいけない。** 同着があると4件以上になる。
       旧実装（`_finish_top3_frames`）は3件でなければ None を返していたため、
       同着のレースが**永久に「未確定」**のまま集計からも落ちていた
       （2026-08-21 立川11R の 7S は 10,000円の外れが回収率から消えていた）。
       当たり目の展開は `keirin_result_top3.winning_combo_labels` が担う。
    """
    out: list[tuple[int, int]] = []
    for e in entries or []:
        fo = e["finish_order"]
        if fo is None or not 1 <= int(fo) <= 3:
            continue
        out.append((int(fo), int(e["frame_no"])))
    return sorted(out)


def _race_payout_display(payouts: Mapping[str, int], won: Sequence[str]) -> tuple[int, int]:
    """一覧に出す「複¥… 単¥…」（そのレースの確定配当）。

    ⚠️ 同着では当たり目が複数あり配当も別々だが、この行は**相場の目安**なので
       先に見つかったものを1つずつ出す。採点（`settle`）は当たり目ごとに正しく
       払戻を積むので、こちらの表示とは独立している。
    """
    trio = next((payouts[c] for c in won if "=" in c and c in payouts), 0)
    tri = next((payouts[c] for c in won if "-" in c and c in payouts), 0)
    return trio, tri


async def _fetch_finishers(
    db: AsyncSession, race_keys: Sequence[str],
) -> dict[str, list[tuple[int, int]]]:
    """race_key → `(着順, 車番)`（3着以内・同着があれば4件以上）。"""
    if not race_keys:
        return {}
    rows: dict[str, list[Any]] = {}
    for e in (await db.execute(
        text("""
            SELECT race_key, frame_no, finish_order FROM keirin.wt_entries
            WHERE race_key = ANY(:keys) AND finish_order BETWEEN 1 AND 3
        """), {"keys": list(race_keys)},
    )).mappings().all():
        rows.setdefault(e["race_key"], []).append(e)
    return {rk: _finishers(v) for rk, v in rows.items()}


async def _fetch_winning_payouts(
    db: AsyncSession, won_by_race: Mapping[str, Sequence[str]],
) -> dict[str, dict[str, int]]:
    """race_key → `{当たり目の表記: 100円あたりの確定払戻}`。

    ⚠️ `wt_odds.combination` は**券種を問わず `-` 区切り**（三連複は昇順）。
       買い目・当たり目の表記（三連複 `1=2=4`）とは別なので変換して引く。
    ⚠️ 同着では当たり目が増える（三連複2通り・三連単はさらに多い）ので
       レースあたり2行決め打ちの引き方をしてはいけない。
    """
    wanted: list[dict[str, str]] = []
    for rk, labels in won_by_race.items():
        for label in labels:
            ordered = "-" in label
            cars = label.split("-" if ordered else "=")
            wanted.append({"rk": rk, "bt": "trifecta" if ordered else "trio",
                           "cb": "-".join(cars), "lb": label})
    if not wanted:
        return {}
    rows = (await db.execute(
        text("""
            SELECT w.rk AS race_key, w.lb AS label, o.odds_value
            FROM jsonb_to_recordset(CAST(:w AS jsonb))
                 AS w(rk text, bt text, cb text, lb text)
            JOIN keirin.wt_odds o
              ON o.race_key = w.rk AND o.bet_type = w.bt AND o.combination = w.cb
        """), {"w": json.dumps(wanted)},
    )).mappings().all()
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        pay = payout_per_100(r["odds_value"])
        if pay:
            out.setdefault(r["race_key"], {})[r["label"]] = pay
    return out


async def _fetch_settled_submissions(
    db: AsyncSession, from_dt: Date, to_dt: Date,
    rank_labels: list[str] | None, *, only_missing_from_picks: bool,
    deleted_only: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """入稿の原本（`bet_detail`）と確定結果から**売った1商品ずつ**を採点して返す。

    netkeirin は1レース1商品なので、ここで返る行は「実際に売ったもの」と1対1になる。

    only_missing_from_picks:
      True  … picks_history に行があるレースを除く（`/stats` の「全入稿」用。
              1レース1行に保つため。理由は下の NOT EXISTS のコメント参照）
      False … **売った全商品**（`/sold-performance` 用）

    returns (行, 買い目が記録されていなかった件数)
    🔴 **`bet_detail` の保存は 2026-08-07 開始**。それ以前の入稿は「入稿した事実」しか
       残っておらず買い目も金額も復元できない。0円として足すと投資額を過小に見せるので
       **集計から外し、件数だけ返す**（黙って落とすと完全な数字に見えてしまう）。
    """
    rank_cond = ""
    params: dict[str, Any] = {"from_date": from_dt.isoformat(), "to_date": to_dt.isoformat()}
    if rank_labels:
        rank_cond = "AND ns.rank_key = ANY(:rank_keys)"
        params["rank_keys"] = rank_labels

    # 同じレースが picks_history にもあるなら、そちらが本体（`/picks` と同じ規則）。
    # ⚠️ **ランク名で突き合わせてはいけない。** 穴埋めは 7A/9A を名乗るため
    #    「7C 候補のレースを 7A で入稿した分」が二重に出る。
    missing_cond = f"""
              AND NOT EXISTS (
                SELECT 1 FROM keirin.picks_history ph2
                WHERE SPLIT_PART(ph2.race_key, '#', 1) = ns.race_key
                  AND ph2.route = 'wt'
                  AND ph2.rank IN {_VALID_PICK_RANKS}
                  AND {_enabled_rank_cond('ph2')}
              )
    """ if only_missing_from_picks else ""
    # 🔴 既定は「売った商品だけ」＝取消は除外。`deleted_only=True` のときだけ
    #    **取り消した分だけ**を採点する（レビュー画面の「取り消した分の参考値」用）。
    #    ⚠️ 両方を混ぜる口は作らない。混ぜると実績サマリーに売っていない分が
    #       紛れ込む（`winning_combos` を取消にも付けたときと同じ事故）。
    deleted_cond = ("ns.deleted_at IS NOT NULL" if deleted_only
                    else "ns.deleted_at IS NULL")

    subs = (await db.execute(
        text(f"""
            SELECT ns.race_key, ns.rank_key, ns.origin, ns.bet_detail, wr.race_date
            FROM keirin.netkeirin_submissions ns
            JOIN keirin.wt_races wr ON wr.race_key = ns.race_key
            WHERE wr.race_date BETWEEN :from_date AND :to_date
              AND {deleted_cond}
              {rank_cond}
              -- 🔴 **「発走から90分」「status=3」で絞らない**（2026-08-25 撤去）。
              --    どちらも「着順と配当が DB に入った」ことを意味しないうえ、
              --    確定が早いレースを 90分間ただ「未確定」にしていた
              --    （その間、確認画面は売った商品に「参考 買っていれば」という
              --     売っていないレース用の文言を出していた）。採点できるかは
              --    `settle()` の `settled` が決めるので、ここで先に切る必要はない。
              {missing_cond}
        """),
        params,
    )).mappings().all()
    if not subs:
        return [], 0

    keys = sorted({s["race_key"] for s in subs})
    finishers = await _fetch_finishers(db, keys)
    won_by_race = {rk: winning_combo_labels(f) for rk, f in finishers.items()}
    payouts = await _fetch_winning_payouts(db, won_by_race)

    out: list[dict[str, Any]] = []
    n_missing = 0
    for s in subs:
        rk = s["race_key"]
        res = settle(_parse_bet_detail(s["bet_detail"]),
                     finishers.get(rk), payouts.get(rk))
        if res.bet <= 0:
            # 買い目が記録されていない（2026-08-07 以前）。件数だけ数えて集計から外す。
            n_missing += 1
            continue
        # 🔴 **採点が終わっていない行は返さない**（2026-08-16）。着順が揃っていない、
        #    あるいは当たっているのに確定配当が引けない状態で返すと
        #    `hit=False` / `payout=0` の行になり、**当たっているレースが
        #    「✗ 不的中」かつ払戻0円として集計**される（実際に発生した）。
        #    落とせば `/review` は「未確定」に、成績側は件数から外れるだけで、
        #    着順・配当が入った次の描画で自然に現れる。
        if not res.settled:
            continue
        out.append({
            "race_key": rk, "rank_key": s["rank_key"], "origin": s["origin"],
            "race_date": str(s["race_date"]),
            "bet": res.bet, "payout": res.payout, "hit": res.hit,
            # 🔴 **netkeirin の表示的中率はこちら**（ガミ＝払戻<賭け金 を不的中と数える）。
            #    素の的中率だけを見ると点数を増やしたときに誤読する。
            "net_hit": res.net_hit,
            "n_combos": res.n_combos,
        })
    return out, n_missing


def _submitted_pick_result(
    bet: dict[str, Any] | None,
    finishers: Iterable[Sequence[int]] | None,
    payouts: Mapping[str, int] | None = None,
) -> Settlement:
    """入稿の原本（`bet_detail`）と確定結果から**売った1商品**を採点する。

    採点そのものは `services/keirin_settlement.settle`（**唯一の正本**）が行う。
    ここはその薄い入口で、呼び出し側が渡す型を揃えるためだけに残してある。

    ⚠️ **買い目は再構成しない。** 入稿の瞬間に保存した combo と stake をそのまま使う。
       傾斜配分は入稿時点の想定オッズで決まるので後から再現できない。

    🔴 **`settled` を必ず見ること。** 「まだ分からない」と「外れ」は別物で、
       `hit=False` だけで判断すると**当たっているレースを不的中として表示・集計**する
       （2026-08-16 に京王閣2Rで発生）。
    """
    return settle(bet, finishers, payouts)


# ---------------------------------------------------------------------------
# 推奨外レースの仮想買い目（hypo_*）— 2026-07-31新設
#
# keirin repo src/strategy_wt.py の s7_select_axis/s7_field_entropy/
# s7_wt_overlap_n と同一ロジックのPython移植（モデル・オッズ不要・
# wt_entries.pred_win_pct/pred_top3_pct/prediction_mark のみから計算できるため
# kiseki backend単独で完結する。keirin repoへの問い合わせ不要）。
# 閾値(S7_AXIS_SUM_MAX等)はモデル生出力(0-1)で較正されているため、
# pred_win_pct/pred_top3_pct（0-100のパーセント値）は使う側で/100すること。
# ---------------------------------------------------------------------------

def _hypo_select_axis(
    win_probs: dict[int, float], top3_probs: dict[int, float],
) -> tuple[int, int, float] | None:
    if not win_probs or not top3_probs or len(win_probs) < 3 or len(top3_probs) < 3:
        return None
    win_top3 = {f for f, _ in sorted(win_probs.items(), key=lambda kv: -kv[1])[:3]}
    place_top3 = {f for f, _ in sorted(top3_probs.items(), key=lambda kv: -kv[1])[:3]}
    overlap = win_top3 & place_top3
    if not overlap:
        return None
    if len(overlap) >= 2:
        cands = sorted(overlap, key=lambda f: -top3_probs[f])
        axis1, axis2 = cands[0], cands[1]
    else:
        axis1 = next(iter(overlap))
        rest = sorted((f for f in top3_probs if f != axis1), key=lambda f: -top3_probs[f])
        if not rest:
            return None
        axis2 = rest[0]
    return axis1, axis2, top3_probs[axis1] + top3_probs[axis2]


def _hypo_field_entropy(top3_probs: dict[int, float]) -> float:
    vals = list(top3_probs.values())
    total = sum(vals)
    if total <= 0:
        return 0.0
    ent = 0.0
    for v in vals:
        s = max(v / total, 1e-9)
        ent -= s * math.log(s)
    return ent


def _hypo_wt_overlap_n(axis1: int, axis2: int, honmei: int | None, taikou: int | None) -> int | None:
    if honmei is None or taikou is None:
        return None
    return len({axis1, axis2} & {honmei, taikou})


@router.get("/picks")
async def get_picks(
    date: str = "",
    include_all: bool = False,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """指定日（YYYY-MM-DD）の推奨ピック一覧を返す。
    include_all=true の場合は推奨外レースも含む全レースを返す。
    S/S+（7PLUS_ST/STP）は 2026-07-15 に全廃（過去分もDB・集計から削除済み）。
    """
    target = date or _today_jst().isoformat()

    if include_all:
        rows = (await db.execute(
            text(f"""
                SELECT
                  wr.race_key                AS base_key,
                  wr.race_no,
                  wr.grade,
                  wr.race_type,
                  wr.start_at,
                  wr.status,
                  wr.n_entries,
                  wr.cup_grade,
                  wr.cup_name,
                  vi.name                    AS venue_name,
                  ph.id,
                  COALESCE(ph.race_key, wr.race_key) AS ph_race_key,
                  -- 入稿だけのレース（ゲート未通過）も推奨として出す。
                  -- 既定ビューと同じ扱いにしないと、同じレースが
                  -- 「全レース表示」でだけ推奨外に見える。
                  COALESCE(ph.rank, 'RANK_' || ns.rank_key) AS rank,
                  (ph.id IS NULL AND ns.rank_key IS NOT NULL) AS submission_only,
                  -- 入稿の出自。バッジ（穴埋め / 手動）の出し分けに使う。
                  ns.origin                  AS origin,
                  ph.pred_combo,
                  ph.n_combos,
                  ph.hit,
                  ph.payout,
                  ph.trio_payout,
                  ph.trifecta_payout,
                  ph.bet_amount,
                  ph.route,
                  COALESCE(ph.miwokuri, FALSE) AS miwokuri,
                  ph.prerace_gami,
                  ph.gap12,
                  ph.gap23,
                  ph.gap34,
                  ph.gate_label,
                  sk.reason_code            AS skip_reason,
                  sk.reason_text            AS skip_reason_text
                FROM keirin.wt_races wr
                JOIN keirin.venue_info vi
                  ON wr.venue_id = vi.venue_code
                -- 生きている入稿のうち最新の1件（取消は論理削除なので除外する）。
                -- 🔴 **picks_history より先に結合する。** 下の JOIN 条件が
                --    ns.rank_key を参照するため、順序を入れ替えると
                --    「missing FROM-clause entry for table ns」で落ちる。
                LEFT JOIN LATERAL (
                    -- 🔴 外側が参照する ns.* は**すべてここで SELECT する**こと。
                    --    抜けると SQL が実行時にしか落ちず、
                    --    「ピックの取得に失敗しました」で画面が丸ごと空になる。
                    SELECT rank_key, origin
                    FROM keirin.netkeirin_submissions x
                    WHERE x.race_key = wr.race_key AND x.deleted_at IS NULL
                    ORDER BY x.submitted_at DESC
                    LIMIT 1
                ) ns ON TRUE
                LEFT JOIN keirin.picks_history ph
                  ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
                 AND ph.race_date = :date
                 AND ph.route = 'wt'
                 AND ph.rank IN {_VALID_PICK_RANKS}
                  AND {_enabled_rank_cond('ph')}
                 -- 🔴 **売ったレースは「売った商品」の行だけを出す**（2026-08-25）。
                 --    netkeirin は1レース1商品で、実測でも生きている入稿は
                 --    どのレースも必ず1件（2026-08-08〜25 の749レース全て）。
                 --    この条件が無いと、候補のランク（例 RANK_7M1）と入稿の
                 --    rank_key（例 7S）が違うレースで**売っていない候補が並び、
                 --    売った商品はどこにも出ない**（実測 704 組）。
                 --    一致する候補行が無ければ ph は NULL になり、
                 --    `submission_only` の行として売った商品が出る。
                 AND (ns.rank_key IS NULL OR ph.rank = 'RANK_' || ns.rank_key)
                -- 入稿しなかった理由（`keirin.submission_skips`）。
                -- 画面の「見送り」バッジに出す。波をまたぐので最新の1件を採る。
                LEFT JOIN LATERAL (
                    SELECT reason_code, reason_text
                    FROM keirin.submission_skips x
                    WHERE x.race_key = wr.race_key
                      AND 'RANK_' || x.rank_key
                          = COALESCE(ph.rank, 'RANK_' || ns.rank_key)
                    ORDER BY x.decided_at DESC
                    LIMIT 1
                ) sk ON TRUE
                WHERE wr.race_date = :date
                ORDER BY wr.start_at, wr.race_no,
                    CASE ph.rank
                      WHEN '7PLUS_CAND' THEN 2
                      ELSE 3
                    END
            """),
            {"date": target},
        )).mappings().all()
    else:
        rows = (await db.execute(
            text(f"""
                SELECT
                  ph.id,
                  ph.race_key,
                  SPLIT_PART(ph.race_key, '#', 1) AS base_key,
                  FALSE AS submission_only,
                  NULL::varchar              AS origin,
                  ph.rank,
                  ph.pred_combo,
                  ph.n_combos,
                  ph.hit,
                  ph.payout,
                  ph.trio_payout,
                  ph.trifecta_payout,
                  ph.bet_amount,
                  ph.route,
                  COALESCE(ph.miwokuri, FALSE) AS miwokuri,
                  ph.prerace_gami,
                  ph.gap12,
                  ph.gap23,
                  ph.gap34,
                  ph.gate_label,
                  wr.race_no,
                  wr.grade,
                  wr.race_type,
                  wr.start_at,
                  wr.status,
                  wr.n_entries,
                  wr.cup_grade,
                  wr.cup_name,
                  vi.name                    AS venue_name
                FROM keirin.picks_history ph
                JOIN keirin.wt_races wr
                  ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
                JOIN keirin.venue_info vi
                  ON wr.venue_id = vi.venue_code
                WHERE ph.race_date = :date
                  AND ph.route = 'wt'
                  AND ph.rank IN {_VALID_PICK_RANKS}
                  AND {_enabled_rank_cond('ph')}

                UNION ALL

                -- 🔴 **手動・穴埋めで入稿したレースも推奨として出す**（2026-08-11）。
                --    ランクのゲートを通っていないと `picks_history` に行が立たないため、
                --    実際に商品として売っているのに一覧にも成績にも現れなかった。
                --    実測でこれが 135 レース（売上シェアの7割）に達していた。
                --    ⚠️ 買い目・的中・投資額は `netkeirin_submissions.bet_detail`
                --       （入稿の瞬間に保存した原本）から組み立てる。再計算しない。
                SELECT
                  NULL::int                  AS id,
                  ns.race_key                AS race_key,
                  ns.race_key                AS base_key,
                  TRUE                       AS submission_only,
                  ns.origin                  AS origin,
                  'RANK_' || ns.rank_key     AS rank,
                  NULL::text                 AS pred_combo,
                  NULL::int                  AS n_combos,
                  NULL::int                  AS hit,
                  NULL::int                  AS payout,
                  NULL::int                  AS trio_payout,
                  NULL::int                  AS trifecta_payout,
                  NULL::int                  AS bet_amount,
                  'wt'                       AS route,
                  FALSE                      AS miwokuri,
                  NULL::numeric              AS prerace_gami,
                  NULL::numeric              AS gap12,
                  NULL::numeric              AS gap23,
                  NULL::numeric              AS gap34,
                  ns.gate_label,
                  wr.race_no,
                  wr.grade,
                  wr.race_type,
                  wr.start_at,
                  wr.status,
                  wr.n_entries,
                  wr.cup_grade,
                  wr.cup_name,
                  vi.name                    AS venue_name
                FROM keirin.netkeirin_submissions ns
                JOIN keirin.wt_races wr
                  ON wr.race_key = ns.race_key
                JOIN keirin.venue_info vi
                  ON wr.venue_id = vi.venue_code
                WHERE wr.race_date = :date
                  -- 取消済みは商品ではない（論理削除なので行は残っている）
                  AND ns.deleted_at IS NULL
                  -- 同じレースが picks_history にもあるなら、そちらが本体。
                  -- ⚠️ ランク名で突き合わせてはいけない。穴埋めは 7A/9A を名乗るため
                  --    「7C 候補のレースを 7A で入稿した分」が二重に出る。
                  AND NOT EXISTS (
                    SELECT 1 FROM keirin.picks_history ph2
                    WHERE SPLIT_PART(ph2.race_key, '#', 1) = ns.race_key
                      AND ph2.race_date = :date
                      AND ph2.route = 'wt'
                      AND ph2.rank IN {_VALID_PICK_RANKS}
                  AND {_enabled_rank_cond('ph2')}
                  )
                ORDER BY start_at, id
            """),
            {"date": target},
        )).mappings().all()

    # 開催（会場×日）の種別。**その開催の第1レース**の発走時刻で決まるので、
    # 推奨レースだけを見ても分からない（当日の全レースから最小を取る必要がある）。
    meeting_type: dict[str, str] = {}
    _first: dict[str, float] = {}
    _venue_of: dict[str, str] = {}
    for m in (await db.execute(
        text("SELECT race_key, venue_id, start_at FROM keirin.wt_races WHERE race_date = :date"),
        {"date": target},
    )).mappings().all():
        _venue_of[m["race_key"]] = str(m["venue_id"])
        h = first_hour_jst(m["start_at"])
        if h is not None:
            v = str(m["venue_id"])
            _first[v] = min(_first.get(v, 1e9), h)
    for rk, v in _venue_of.items():
        t = meeting_type_of_first_hour(_first.get(v))
        if t:
            meeting_type[rk] = t

    # 入稿時の買い目・金額配分（keirin 側が**入稿の瞬間に**保存した値）。
    # 傾斜配分は入稿時点の想定オッズから決まるため**あとから再現できない**ので、
    # ここは記録を読むだけにする（再計算してはいけない）。
    # 🔴 **取消（論理削除）かどうかを持って回る。** 取り消した入稿は商品ではないので
    #    的中・払戻・投資額をそこから作ってはいけない。買い目は「何を出そうとしたか」
    #    の記録として残すが、画面には取消と分かるように出す。
    submitted = {
        (m["race_key"], m["rank_key"]): (m["bet_detail"], m["deleted_at"] is not None,
                                         m["cancel_reason"])
        for m in (await db.execute(
            text("""
                SELECT ns.race_key, ns.rank_key, ns.bet_detail, ns.deleted_at,
                       ns.cancel_reason
                FROM keirin.netkeirin_submissions ns
                JOIN keirin.wt_races wr ON wr.race_key = ns.race_key
                WHERE wr.race_date = :date
                -- dict にするので**最後に来た行が勝つ**。取消を先に、生きている
                -- 入稿を後に並べ、同じ状態なら新しいほうを後にする。
                ORDER BY (ns.deleted_at IS NULL) ASC, ns.submitted_at ASC
            """),
            {"date": target},
        )).mappings().all()
    }

    # 確定着順と当たり目の配当は**レース単位でまとめて引く**。行ごとに引くと
    # 1日40レースで往復が倍増する（同じレースに複数ランクの行が立つこともある）。
    _base_keys = sorted({r["base_key"] for r in rows})
    finishers_by_race = await _fetch_finishers(db, _base_keys)
    won_by_race = {rk: winning_combo_labels(f) for rk, f in finishers_by_race.items()}
    payouts_by_race = await _fetch_winning_payouts(db, won_by_race)

    picks = []
    for r in rows:
        base_key = r["base_key"]
        has_pick = r["rank"] is not None
        # 入稿記録だけの行（ゲート未通過で picks_history に無い）。
        submission_only = bool(r.get("submission_only"))

        is_wide = has_pick and r["rank"] == "WIDE"
        race_key = (r["ph_race_key"] if include_all else r["race_key"]) if has_pick else base_key

        entries = (await db.execute(
            text("""
                SELECT
                  frame_no,
                  name,
                  race_point,
                  style,
                  line_pos,
                  line_group,
                  finish_order,
                  player_class,
                  pred_win_pct,
                  pred_top2_pct,
                  pred_top3_pct,
                  prediction_mark
                FROM keirin.wt_entries
                WHERE race_key = :race_key
                ORDER BY frame_no
            """),
            {"race_key": base_key},
        )).mappings().all()

        # 確定した3着以内（同着があれば4件以上）と、その当たり目。
        # 🔴 判定は `keirin_result_top3` が正本。フロントで組み立て直さない。
        finishers = finishers_by_race.get(base_key, [])
        won = won_by_race.get(base_key, [])

        # 当たり目の確定配当（100円あたり）。wt_odds の最終オッズから引き、
        # 引けない分だけ picks_history の記録で補う（10円単位切り捨て。実払戻との
        # 一致は 2026-07-12 に検証済み）。
        pays = dict(payouts_by_race.get(base_key, {}))
        if has_pick:
            # ⚠️ 同着で当たり目が複数あるときは補わない。picks_history の払戻は
            #    1つしか持っておらず、どちらの目のものか決められないため。
            trios = [c for c in won if "=" in c]
            tris = [c for c in won if "-" in c]
            if len(trios) == 1 and r["trio_payout"] and trios[0] not in pays:
                pays[trios[0]] = int(r["trio_payout"])
            if len(tris) == 1 and r["trifecta_payout"] and tris[0] not in pays:
                pays[tris[0]] = int(r["trifecta_payout"])
        trio_pay, trifecta_pay = _race_payout_display(pays, won)

        # 入稿の原本（keirin 側が入稿の瞬間に保存した買い目と金額配分）。
        submitted_raw, submission_cancelled, cancel_reason = submitted.get(
            (base_key, (r["rank"] or "").replace("RANK_", "")), (None, False, None))
        submitted_bet = _parse_bet_detail(submitted_raw)
        # 🔴 **この行が「売った商品」かどうか**（2026-08-25）。取消したものは
        #    売っていないので False。以前は `picks_history.bet_amount > 0` を
        #    フロントが購入判定に使っていたが、あれは**ゲートを通る前の候補**にも
        #    立つ値で、平均払戻ゲート等で見送ったレースまで「購入・的中」と
        #    表示していた（08-25 松阪7R 7S ＝ 想定平均 19,226円 で見送ったのに
        #    「的中 42,400円」と出ていた）。
        sold = bool(submitted_bet) and not submission_cancelled
        # 🔴 **売った商品があるなら、買い目も投資も的中も払戻もそこから作る**
        #    （2026-08-25）。以前は「picks_history に行が無い入稿」だけをこの経路に
        #    通し、行があるレースは `picks_history.hit`（＝ランクの**候補**の成績）を
        #    出していた。候補と売った商品は別物で、2026-08-07〜25 の売った295商品の
        #    うち **53件（18%）で的中の表示が食い違っていた**
        #    （例: 08-25 防府8R 7S は Discord「🎯 15,200円」に対し一覧が「✗」）。
        #    取消した入稿は商品ではないので通さない（買い目だけ記録として出す）。
        sub_result = (settle(submitted_bet, finishers, pays)
                      if submitted_bet and not submission_cancelled else None)

        # 合成オッズも**表に出す買い目と同じもの**から出す。売った商品があるなら
        # その買い目、無ければ picks_history の候補（`pred_combo`）から計算する。
        if sub_result is not None and submitted_bet:
            synth_odds = await _calc_synth_odds_from_lines(
                db, base_key, submitted_bet["lines"])
        elif has_pick:
            synth_odds = await _calc_synth_odds(db, base_key, r["pred_combo"], is_wide)
        else:
            synth_odds = None

        # 推奨外レースの仮想買い目（hypo_*）。7/9車のみ・軸選定可能な場合のみ非null。
        hypo_axis1 = hypo_axis2 = hypo_others = hypo_axis_sum = hypo_entropy = hypo_wt_overlap_n = None
        if not has_pick and r["n_entries"] in (7, 9):
            win_probs = {int(e["frame_no"]): float(e["pred_win_pct"]) for e in entries
                         if e["pred_win_pct"] is not None}
            top3_probs = {int(e["frame_no"]): float(e["pred_top3_pct"]) for e in entries
                          if e["pred_top3_pct"] is not None}
            sel = _hypo_select_axis(win_probs, top3_probs)
            if sel is not None:
                hypo_axis1, hypo_axis2, hypo_axis_sum = sel
                hypo_others = sorted(
                    int(e["frame_no"]) for e in entries
                    if int(e["frame_no"]) not in (hypo_axis1, hypo_axis2)
                )
                hypo_entropy = _hypo_field_entropy(top3_probs)
                honmei = next((int(e["frame_no"]) for e in entries if e["prediction_mark"] == 1), None)
                taikou = next((int(e["frame_no"]) for e in entries if e["prediction_mark"] == 2), None)
                hypo_wt_overlap_n = _hypo_wt_overlap_n(hypo_axis1, hypo_axis2, honmei, taikou)

        picks.append({
            "id": r["id"],
            "race_key": race_key,
            "has_pick": has_pick,
            "venue_name": r["venue_name"],
            "race_no": r["race_no"],
            "grade": r["grade"],
            "race_type": r["race_type"],
            # 看板レース（決勝・特選クラス）。Web一覧の★表示に使う。
            # 判定の唯一の正本は services/keirin_marquee.py
            # （入稿側 keirin/src/marquee.py もそこを読む・2026-08-11 一本化）
            "is_marquee": is_marquee_race(r["race_type"]),
            "start_at": r["start_at"],
            "status": r["status"],
            "n_entries": r["n_entries"],
            "rank": r["rank"],
            "display_rank": _display_rank(str(r["rank"])) if has_pick else None,
            # 買い目は「売った商品」があればそれ、無ければ候補の買い目を
            # **参考として**出す（買っていないことは `sold` で表す）。
            "pred_combo": (sub_result.pred_combo if sub_result
                           else (r["pred_combo"] if has_pick else None)),
            "n_combos": (sub_result.n_combos if sub_result
                         else (r["n_combos"] if has_pick else None)),
            "synth_odds": synth_odds,
            # 🔴 **売った商品かどうか。フロントの購入判定はこれだけを見る**
            #    （2026-08-25）。`bet_amount > 0` で判定してはいけない。
            "sold": sold,
            # 入稿しなかった理由（`keirin.submission_skips`）。売っていない行に
            # だけ意味がある。
            # 🔴 **文言はサーバーが決める**。語彙の正本は
            #    services/keirin_skip_reasons.py で、入稿側（keirin）も同じ
            #    ファイルを読む。フロントで日本語を組み立てると三重管理になる。
            "skip_reason": (None if sold else (r.get("skip_reason") or None)),
            "skip_reason_label": (
                None if sold or not r.get("skip_reason")
                else skip_reason_label(r.get("skip_reason"))),
            "skip_reason_text": (
                None if sold or not r.get("skip_reason")
                else skip_reason_describe(r.get("skip_reason"),
                                          r.get("skip_reason_text"))),
            # 取り消した理由（`netkeirin_submissions.cancel_reason`）。
            "cancel_reason": cancel_reason if submission_cancelled else None,
            # ⚠️ 採点が終わるまでは的中にしない（`settled` の意味は
            #    `services/keirin_settlement` の docstring 参照）。
            # 🔴 **売っていない行は的中にしない。** 候補が当たったかどうかは
            #    商品の成績ではない（`winning_combos` と参考買い目から画面側で
            #    「買っていれば当たっていた」と読める形にはしてある）。
            "hit": bool(sub_result.hit and sub_result.settled) if sub_result else False,
            # 採点が終わったか。🔴 **`hit=False` と混ぜない。** これを見ずに描画すると
            #    確定前・配当待ちのレースが「✗ 不的中」として出る。
            "settled": (sub_result.settled if sub_result else bool(won)),
            # 確定した当たり目（同着なら複数）。買い目のどれが当たったかの色付けは
            # これとの一致だけで決める。🔴 フロントで着順から組み立て直さない
            # （同着を必ず取りこぼす）。
            "winning_combos": won,
            # 🔴 **売っていない行の投資・払戻は 0**（2026-08-25）。
            #    `picks_history` の bet_amount / payout は「1万円賭けたことにしたら」
            #    という**ペーパーの名目値**で、売上にも収支にも対応しない。
            #    ここでフォールバックすると一覧の集計が実売とずれる
            #    （8月は毎日 26〜49件が売っていないのに購入表示されていた）。
            "payout": sub_result.payout if sub_result else 0,
            "trio_payout": trio_pay,
            "trifecta_payout": trifecta_pay,
            "bet_amount": sub_result.bet if sub_result else 0,
            # ゲートを通っていない入稿（手動・看板の穴埋め）であることを表に出す。
            # 混ぜたまま出すと「ランクの成績」と読まれてしまう。
            "submission_only": submission_only,
            # 入稿の出自。`submission_only` だけだと**看板の穴埋め（自動）も
            # 手動入稿も同じに見える**ので、バッジの出し分けにはこちらを使う。
            "origin": r.get("origin") or None,
            # 開催グレード（GP/GI/GII/GIII/FI/FII）。⚠️ `grade` 列は級班なので別物。
            "cup_grade": r.get("cup_grade"),
            "cup_grade_label": grade_label(r.get("cup_grade")),
            # レース信頼度（0〜100%）。100% ＝ 上位2車の3着内率合計 2.00。
            # **ランクのゲートが見ているのと同じ量**なので、出る／出ないの理由が
            # 画面から読める。正本は keirin 側 `src/p3_calibration.confidence_pct`。
            "confidence_pct": confidence_from_entries(
                entries, r.get("race_type"), r.get("cup_grade")),
            # 信頼度が見ている2車のうち何車が3着以内に入ったか（0/1/2・確定後のみ）。
            # 表示は 2→○ / 1→△ / 0→×。**1軸だけの的中も情報**なので潰さない。
            # 🔴 買い目の的中とは別物。相手が外れても二軸はそろっていることがある。
            "confidence_hit_count": confidence_hit_count_from_entries(entries),
            "cup_name": r.get("cup_name"),
            "miwokuri": bool(r["miwokuri"]) if has_pick else False,
            "prerace_gami": float(r["prerace_gami"]) if (has_pick and r["prerace_gami"] is not None) else None,
            "gap12": float(r["gap12"]) if (has_pick and r.get("gap12") is not None) else None,
            "gap23": float(r["gap23"]) if (has_pick and r.get("gap23") is not None) else None,
            "gap34": float(r["gap34"]) if (has_pick and r.get("gap34") is not None) else None,
            "gate_label": r["gate_label"] if has_pick else None,
            "hypo_axis1": hypo_axis1,
            "hypo_axis2": hypo_axis2,
            "hypo_others": hypo_others,
            "hypo_axis_sum": hypo_axis_sum,
            "hypo_entropy": hypo_entropy,
            "hypo_wt_overlap_n": hypo_wt_overlap_n,
            "meeting_type": meeting_type.get(base_key),
            "submitted_bet": submitted_bet,
            # 入稿を取り消した（＝売っていない）。買い目は記録として残すが、
            # 的中・払戻・投資額はここから作らない。画面でも取消と分かるように出す。
            "submission_cancelled": submission_cancelled and submitted_bet is not None,
            "entries": [
                {
                    "frame_no": e["frame_no"],
                    "name": e["name"],
                    "race_point": e["race_point"],
                    "style": e["style"],
                    "line_pos": e["line_pos"],
                    "line_group": e["line_group"],
                    "finish_order": e["finish_order"],
                    "player_class": e["player_class"],
                    "pred_win_pct": float(e["pred_win_pct"]) if e["pred_win_pct"] is not None else None,
                    # 2着内率。列追加（2026-08-12）以降に算出したレースだけ値が入る
                    "pred_top2_pct": float(e["pred_top2_pct"]) if e["pred_top2_pct"] is not None else None,
                    "pred_top3_pct": float(e["pred_top3_pct"]) if e["pred_top3_pct"] is not None else None,
                    "prediction_mark": e["prediction_mark"],
                }
                for e in entries
            ],
        })

    return JSONResponse(content=picks)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def _make_period_dict(
    n_picks: int, n_hits: int, total_bet: int, total_payout: int,
    max_payout: int | None = None,
) -> dict:
    roi = round(total_payout / total_bet, 3) if total_bet > 0 else None
    return {
        "n_picks": n_picks,
        "n_hits": n_hits,
        "total_bet": total_bet,
        "total_payout": total_payout,
        "roi": roi,
        "max_payout": max_payout,
    }


_SETTLED_COND = """(
    wr.status = 3
    OR (wr.start_at IS NOT NULL AND wr.start_at::BIGINT + 5400 < EXTRACT(EPOCH FROM NOW()))
)"""

# 廃止済みだが**実際に売った**ランクの表示名（2026-08-16 追加）。
#
# 🔴 これが無いと `_display_rank()` が内部名（`RANK_7A` 等）をそのまま返し、
#    フロントの `RANK_STYLE` に該当キーが無いので **「非」バッジ**になる。
#    実害: 2026-08-14 に 7A を RANK_7S へ統合した直後から、`netkeirin_submissions`
#    の 7A(137件)・7SS(31件)・9A(13件)・9S(1件) = **182件の実入稿が全部「非」**に
#    なっていた（例: 2026-08-14 岐阜1R `20260814_43_01`）。
#
# 🔴 **`_PAPER_RANK_LABELS` へ足してはいけない。** あちらは
#    `_VALID_PICK_RANKS` / `_RANKS_ALL` の allowlist を導出しており、足すと
#    `picks_history` に残っている廃止ランクの行（RANK_9A 1,046件・RANK_9S 179件・
#    SEVEN_S1 3件）が**過去日の集計に遡って混ざる**。表示名だけを与える。
#
# ⚠️ 後継ランクへ寄せない（7A→7S 等にしない）。売ったのはその当時の商品なので、
#    寄せると廃止済みランクの成績が現行ランクの成績として読まれる。
#
# なぜ入稿側だけ起きるか: `picks_history` 由来の行は allowlist で落ちるが、
# 入稿だけの行（ゲート未通過・看板の穴埋め）は SQL が `'RANK_' || ns.rank_key` で
# rank を合成しており allowlist を通らない。**売った事実は消せないので落とさず、
# 表示名を与えるのが正しい**（2026-08-11 に「売っているのに一覧に出ない」を
# 直した経緯と同じ方針）。
_LEGACY_RANK_LABELS: dict[str, str] = {
    "RANK_7A": "7A",      # 2026-08-14 に RANK_7S へ統合
    "RANK_7SS": "7SS",    # 2026-08-02 全廃 → 2026-08-14 に RANK_7S へ統合
    "RANK_9A": "9A",      # 2026-08-14 に RANK_9C へ集約
    "RANK_9S": "9S",      # 2026-08-14 に RANK_9C へ集約
    "SEVEN_S1": "S1",     # 2026-07-31 全廃
}


def _display_rank(rank: str) -> str:
    """DB の内部 rank から、フロントエンドが表示に使う表示ランク文字列を返す。

    2026-08-01〜: keirin側のランク全面改名（内部rank="RANK_"+表示ラベル方式へ
    統一。commit f31f84b）に伴い、_PAPER_RANK_LABELS の単純な1対1マッピングへ
    一本化した。

    旧実装は SEVEN_S7/NINE_S9 を gate_label('SS'/'S') で 7SS/9SS・7S/9S に
    分岐していたが、この分岐は keirin側 commit e994758（2026-07-31）で廃止済み
    （rank_7s_gate_label() は常に "S" のみを返す。既存行の gate_label も 'S' へ
    一括更新済み）。gate_label カラム自体はDBに残っており過去データ分析用に
    保持するが、表示ランクの決定には一切使わない。

    廃止済みでも**実際に入稿した**ランクは `_LEGACY_RANK_LABELS` で表示名を
    与える（入稿だけの行は allowlist を通らずここへ到達するため）。

    それでも未知の rank は元の文字列をそのまま返す。
    🔴 その場合フロントは「非」バッジになる。**内部名が画面へ漏れたら
       `_LEGACY_RANK_LABELS` の追加漏れ**なので、回帰テストで縛ってある
       （`test_keirin_rank_consistency.py`）。
    """
    if rank in _PAPER_RANK_LABELS:
        return _PAPER_RANK_LABELS[rank]
    return _LEGACY_RANK_LABELS.get(rank, rank)


# トップライン（当日/当月/当年）は現行有効ランク全て（7S/7A/9S/9A/7SS）をまとめて
# 表示する（2026-07-27にユーザー要望で7車+9車+境界ランクを統合した方針を継続。
# 2026-08-01にRANK_7SSを追加したが、2026-08-02に全廃した（上記 _PAPER_RANK_LABELS 参照）＝
# ユーザー要望「7SS の追加を行ったため VPS の 7SS 表示も有効にして下さい」への
# 対応）。by_rank（_aggregate内部で_display_rank()により算出）にはこれら全ランク
# が同じ辞書に並ぶため、フロントエンドの「ランク別」展開でまとめて確認できる。
_RANKS_ALL = "(" + ", ".join(f"'{r}'" for r in _PAPER_RANK_LABELS) + ")"


# ---------------------------------------------------------------------------
# 入稿対象OFFのランクは Web の集計・表示からも外す（2026-08-12・ユーザー要望）
#
# 「もう売っていないランクの成績が Web に並び続ける」のを避ける。判定は
# `keirin.netkeirin_settings.enabled` の1点だけを見るので、`/keirin/settings`
# でトグルすればコード変更もデプロイも要らずに表示が追随する。
#
# 🔴 **fail-open にする**（行が無い＝表示する）。keirin 側の
#    `netkeirin_submit_wt._is_enabled()` と同じ規約で、片方だけ fail-closed に
#    すると「入稿はされているのに Web には出ない」ランクが静かに生まれる。
#
# ⚠️ picks_history.rank は `RANK_7C`、netkeirin_settings.rank_key は `7C` と
#    綴りが違う。`'RANK_' || rank_key` で突き合わせること。
# ⚠️ 全廃済みランク（SEVEN_S1 等）は元々 allowlist（_RANKS_ALL）で落ちるので
#    ここでは扱わない。
_DISABLED_RANK_EXCLUSION = """NOT EXISTS (
                SELECT 1 FROM keirin.netkeirin_settings s
                WHERE 'RANK_' || s.rank_key = {alias}.rank AND s.enabled = FALSE
              )"""


def _enabled_rank_cond(alias: str = "ph") -> str:
    """入稿対象OFFのランクを除外する WHERE 断片を返す。"""
    return _DISABLED_RANK_EXCLUSION.format(alias=alias)


async def visible_rank_labels(db: AsyncSession) -> list[str]:
    """Web に出してよい表示ラベル（入稿対象ONのもの）を定義順で返す。

    フロントの「ランク別」展開・絞り込みチップはこの一覧で絞る。
    ⚠️ ここも fail-open（`netkeirin_settings` に行が無いランクは出す）。
    """
    rows = (await db.execute(text(
        "SELECT rank_key FROM keirin.netkeirin_settings WHERE enabled = FALSE"
    ))).scalars().all()
    off = {f"RANK_{k}" for k in rows}
    return [label for internal, label in _PAPER_RANK_LABELS.items() if internal not in off]


async def _aggregate(
    db: AsyncSession,
    where: str,
    params: dict[str, Any],
    rank_filter: str = _RANKS_ALL,
    *,
    from_dt: Date | None = None,
    to_dt: Date | None = None,
) -> dict:
    """サマリーの1期間ぶん。

    🔴 **投資・払戻・的中は「実際に売った商品」から数える**（2026-08-19）。
       以前は `picks_history` の `bet_amount > 0` を母集団にしていたが、これは
       **売った商品と一致しない**:

         - 看板レースの穴埋め入稿は**ランクのゲートを通っていないので行が立たない**
         - `bet_amount` が入るのは発走前判定を通った分だけで、当日はほとんど 0 のまま
           （翌朝の再構築で埋まる）

       実測 2026-08-19: 売った40件のうちサマリーが数えていたのは **4件**だけで、
       一覧（`/keirin`・`/keirin/review`）は40件すべてを出しているのに
       サマリーだけ別の母集団を見ていた。

       母集団は `/sold-performance` と同じ `_fetch_settled_submissions
       (only_missing_from_picks=False)`＝netkeirin へ出した1商品＝1行。

    ⚠️ **買い目の原本（`bet_detail`）は 2026-08-07 から**。それ以前の入稿は
       金額を復元できないので集計に入れず、`n_unpriced` として件数だけ返す
       （0円で足すと投資額を過小に見せる）。当年の数字はこの日以降が対象になる。

    ⚠️ **候補数（`n_candidates`）は従来どおり `picks_history` から数える。**
       「候補」はゲート通過前の紙の概念で、売った商品とは別物。
    """
    # 2026-08-01〜: 現行ランクは _PAPER_RANK_LABELS の5ランク（RANK_7S/RANK_7A/
    # RANK_9S/RANK_9A）。gate_labelによる表示分岐は廃止済み（_display_rank
    # 参照）。旧S1(SEVEN_S1)・旧S2=7PLUS_U・旧S3=7PLUS_M は全廃・行はアーカイブ
    # 退避 or 残骸のまま（allowlist方式のため自動的に集計対象から除外される）。
    # rank_filter: 個別ランクだけの集計にも本関数を再利用できるようパラメータ化
    # （既定は現行有効ランク全て）。
    # ⚠️ ランク絞り込み（`rank_filter`）は候補数の集計にだけ使う。実売側は
    #    netkeirin の rank_key で持っており、無効化ランクは入稿されないので
    #    そもそも行が立たない。
    sold, n_unpriced = (
        await _fetch_settled_submissions(db, from_dt, to_dt, None,
                                         only_missing_from_picks=False)
        if from_dt and to_dt else ([], 0))

    def _totals(items: list[dict[str, Any]]) -> dict:
        won = [i["payout"] for i in items if i["hit"]]
        return _make_period_dict(
            len(items), len(won),
            sum(i["bet"] for i in items),
            sum(i["payout"] for i in items),
            max(won) if won else None)

    result = _totals(sold)
    result["n_unpriced"] = n_unpriced

    # 総候補レース数（判定前候補+見送り含む・対象ランクの distinct レース数）
    # write_candidates_wt が朝の候補選定時点で書き込む行を数えるため、結果確定前
    # （_SETTLED_COND）でもカウント対象に含める（2026-07-27: 朝時点でカウントされない
    # 不具合修正・的中/回収額はレース確定後でないと分からないため他の集計とは分離）。
    cand_row = (await db.execute(
        text(f"""
            SELECT COUNT(DISTINCT SPLIT_PART(ph.race_key, '#', 1)) AS n_candidates
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE {where}
              AND ph.route = 'wt'
              AND ph.rank IN {rank_filter}
              AND {_enabled_rank_cond()}
        """),
        params,
    )).mappings().one_or_none()
    result["n_candidates"] = int(cand_row["n_candidates"] or 0) if cand_row else 0

    # ランク別集計（全てペーパー・名目賭金）: RANK_7S/RANK_7A/RANK_9S/RANK_9A の4ランク。
    # 2026-08-01〜: gate_labelはもう表示ランクを分岐しない（_display_rank参照）ため
    # GROUP BY からも外す。gate_labelでGROUP BYしたまま_display_rank()で複数行が
    # 同じ表示キーに収束すると、Python側のdict代入（by_rank[key] = ...）が
    # 後勝ちで上書きしてしまい集計が欠落する事故になるため（例: RANK_7Sは
    # gate_label='S'/'SS'の2行に分かれて残っているが、表示上は"7S"1つに統合される）。
    # ランク別も同じ母集団（実売）で割る。ここだけ picks_history に戻すと
    # 合計とランク別の和が合わなくなる。
    by_rank_items: dict[str, list[dict[str, Any]]] = {}
    # ⚠️ 変数名に `r` を使わない。下の `for r in paper_cand_rows` が RowMapping で
    #    束縛するため、同名だと mypy が代入不能として落ちる（既出の型衝突）。
    for sr in sold:
        by_rank_items.setdefault(_display_rank(f"RANK_{sr['rank_key']}"), []).append(sr)
    by_rank: dict[str, dict] = {k: _totals(v) for k, v in by_rank_items.items()}

    # ランク別候補数 = 見送り含む全行の distinct レース数
    # （write_candidates_wt が候補時点で #CAND 行（rank='7PLUS_CAND'）を書き込み、
    # 発走前オッズ確定時に #7S/#7A/#9S/#9A 等へ上書きされる。結果確定前でも
    # カウント対象に含める＝上の cand_row と同じ理由で _SETTLED_COND は付けない）
    paper_cand_rows = (await db.execute(
        text(f"""
            SELECT ph.rank AS rank,
                   COUNT(DISTINCT SPLIT_PART(ph.race_key, '#', 1)) AS n_candidates
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE {where}
              AND ph.route = 'wt'
              AND ph.rank IN {rank_filter}
              AND {_enabled_rank_cond()}
            GROUP BY ph.rank
        """),
        params,
    )).mappings().all()
    for r in paper_cand_rows:
        key = _display_rank(str(r["rank"]))
        n_cand = int(r["n_candidates"] or 0)
        if key not in by_rank and n_cand > 0:
            by_rank[key] = _make_period_dict(0, 0, 0, 0)
        if key in by_rank:
            by_rank[key]["n_candidates"] = n_cand
    result["by_rank"] = by_rank
    return result


@router.post("/refresh")
async def refresh_picks(_: ApiKeyDep, date: str = "") -> JSONResponse:
    """当日採点を keirin ホスト側の正本スクリプトで即時実行する（webhook 中継）。

    旧実装はこの API 内で独自採点していたが、prerace_decisions を正本とする
    keirin 側 notify_results_wt.py と判定が二重実装になり、新ランク体系
    (7PLUS_ST/STP・S+ 200円/点) への追随漏れ・rank='7PLUS_CAND' のまま
    書き戻してサマリー集計から漏れるバグを抱えていたため、2026-07-12 に
    keirin-webhook /fetch-results（intraday_results_wt.sh →
    notify_results_wt.py）への中継に一本化した。
    採点は常に「当日」に対して行われる（過去日の再採点は keirin 側で
    scripts/notify_results_wt.py を直接実行すること）。
    """
    today = _today_jst().isoformat()
    note = ""
    if date and date != today:
        note = f"（注: 採点は当日({today})分のみ実行されます。過去日({date})の再採点は keirin 側スクリプトで行ってください）"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_WEBHOOK_BASE}/fetch-results", timeout=10.0)
            body = r.json()
            msg = str(body.get("message", "採点ジョブを起動しました"))
            return JSONResponse(
                content={"ok": bool(body.get("ok", r.status_code < 400)),
                         "message": msg + note},
                status_code=r.status_code,
            )
    except Exception as exc:
        return JSONResponse(
            content={"ok": False, "message": f"採点ジョブの起動に失敗しました: {exc}"},
            status_code=503,
        )


@router.post("/fetch-odds")
async def trigger_fetch_odds(_: ApiKeyDep) -> JSONResponse:
    """発走前ガミ判定を即時実行する（keirinホスト側スクリプトをバックグラウンド起動）。"""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_WEBHOOK_BASE}/fetch-odds", timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "message": str(exc)}, status_code=503)


@router.post("/fetch-results")
async def trigger_fetch_results(_: ApiKeyDep) -> JSONResponse:
    """当日結果を即時取得する（keirinホスト側スクリプトをバックグラウンド起動）。"""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_WEBHOOK_BASE}/fetch-results", timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "message": str(exc)}, status_code=503)


_RACE_KEY_RE = re.compile(r"^\d{8}_\d{2}_\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# 推奨外レースの手動入稿で選べるランク。
# S1（2026-07-31全廃）に加え、旧gate_label分岐由来の7SS/9SS（同日廃止・SSはSへ
# 統合済み）も対象外。RANK_7SS（2026-07-31新設の独立ランク）はkiseki側で
# hypo軸選定（_hypo_select_axis、単勝/複勝指数トップ3重なり方式＝S7/S9と同じ
# ロジック）を実装済みだが、これは実際の7SSの軸選定（race_point単独top1×
# WT公式印◯△✕由来の別ロジック・rank_7ss_select_axis）とは異なるため、誤った
# 軸を「7SS」として入稿してしまうリスクを避けてあえて対象外にしている
# （2026-08-01時点の判断。7SS専用のhypo軸ロジックを別途移植すれば追加可能）。
#
# 【2026-08-05】新設の RANK_7SS（entropy不合格×同一ライン）は軸選定・買い目とも
# 7S/7A と同一なので技術的には追加できるが、netkeirin の「自信あり」タグ
# （7SSのみ付与・上限1件/日と推定）を手動入稿で消費すると自動入稿側が落ちるため
# あえて対象外のままにしている。
#
# 【2026-08-08 解消】以前はフロントの MANUAL_SUBMIT_RANKS と api.ts の
# ManualKeirinRankKey が "7B" を含む一方ここには無く、**UI で選べるのに送信すると
# 必ず 400「不正なrank_key」**になっていた（2026-08-03 の 7B 新設以来）。
# 7B は hypo軸（_hypo_select_axis）と本番の軸選定が一致するか未確認なので
# ここへ足すのではなく、フロントの選択肢から 7B を落として解消した。
# 7B を手動入稿したくなったら先に軸の一致を確認し、フロントと両方へ足すこと。
#
# ⚠️ この tuple は**フロントの MANUAL_SUBMIT_RANKS / ManualKeirinRankKey の
#    上位集合**でなければならない。test_keirin_rank_consistency.py が検査する。
# 手動入稿で選べるランク。9車は 2026-08-14 に 9S/9A を廃止し 9C へ集約した。
# ⚠️ keirin 側 `MANUAL_ALLOWED_RANKS` と**必ず一致させること**
#    （`test_frontend_manual_submit_ranks_match_backend` が突き合わせている）。
# 🔴 7A は 2026-08-14 に RANK_7S へ統合したので外した。
_MANUAL_RANK_KEYS = ("7S", "7B", "9C")


class SubmitRaceIn(BaseModel):
    race_key: str
    date: str
    session: str
    # 推奨外レースの手動入稿用（2026-07-31新設）。3つとも指定時のみ有効。
    # 未指定なら従来通りkeirin側の候補JSON検索に任せる（推奨レースの挙動は不変）。
    rank_key: str | None = None
    axis1: int | None = None
    axis2: int | None = None


@router.post("/submit-race")
async def trigger_submit_race(body: SubmitRaceIn, _: ApiKeyDep) -> JSONResponse:
    """指定レース1件のみをnetkeirinへピンポイント入稿する（keirinホスト側の通常入稿
    スクリプト(netkeirin_submit_wt.py --race-key)をrace_key絞り込みで起動する中継。
    ON/OFF・テンプレート・ゲート・重複送信防止は通常の日次/夕方バッチと完全に同一ルール）。

    rank_key/axis1/axis2 が揃っている場合は、推奨外レース（has_pick=false）を
    ユーザーがダイアログでランク選択して手動入稿するケース。keirin側の候補JSON
    検索を経由せず、指定した軸2車・ランクで直接入稿する
    （netkeirin_submit_wt.py --manual-rank-key/--axis1/--axis2）。

    /keirin/picks 等が返す race_key は候補種別を示す "#CAND"/"#7S" 等のサフィックスを
    含む場合がある（本ルーター内の各クエリが SPLIT_PART(race_key, '#', 1) で剥がしている
    のと同じ理由）。keirin側の候補ファイルはサフィックス無しの物理レースキーのみを持つため、
    ここでも同様に剥がしてから検証・中継する。
    """
    base_race_key = body.race_key.split("#", 1)[0]
    if not _RACE_KEY_RE.match(base_race_key):
        return JSONResponse(content={"ok": False, "message": f"不正なrace_key: {body.race_key}"}, status_code=400)
    if not _DATE_RE.match(body.date):
        return JSONResponse(content={"ok": False, "message": f"不正な日付: {body.date}"}, status_code=400)
    if body.session not in ("morning", "evening"):
        return JSONResponse(content={"ok": False, "message": f"不正なsession: {body.session}"}, status_code=400)

    payload: dict[str, Any] = {"race_key": base_race_key, "date": body.date, "session": body.session}
    if body.rank_key is not None or body.axis1 is not None or body.axis2 is not None:
        if body.rank_key not in _MANUAL_RANK_KEYS:
            return JSONResponse(content={"ok": False, "message": f"不正なrank_key: {body.rank_key}"}, status_code=400)
        if body.axis1 is None or body.axis2 is None or body.axis1 == body.axis2:
            return JSONResponse(content={"ok": False, "message": "axis1/axis2が不正です"}, status_code=400)
        payload["rank_key"] = body.rank_key
        payload["axis1"] = body.axis1
        payload["axis2"] = body.axis2

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_WEBHOOK_BASE}/submit-race", json=payload, timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "message": str(exc)}, status_code=503)


@router.get("/stats")
async def get_stats(
    from_date: str = "",
    to_date: str = "",
    granularity: str = "daily",
    rank: str = "all",
    include_manual: bool = False,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """日別 / 月別の投資・回収・累積ROI推移を返す。

    🔴 **母集団は「実際に売った商品」だけ**（2026-08-25 統一）。`picks_history`
       （ランクの候補）は使わない。以前は候補の名目投資1万円を積んでおり、
       ゲートで見送ったレースまで収支に入っていた。

    include_manual: **もう効かない**（互換のため受け取るだけ）。以前は
        「ゲートを通った推奨だけ / 全入稿」の切り替えだったが、売った商品に
        揃えた時点で区別が無くなった（売ったものは経路を問わず全部入る）。

    granularity: "daily"（日別）または "monthly"（月別）
    from_date / to_date: YYYY-MM-DD 形式。省略時は直近30日。
    rank: 集計対象ランク。カンマ区切りで複数指定可（例: "7A,9S"）。
          受け付ける値は _PAPER_RANK_LABELS の表示ラベル（2026-08-06時点で
          "7SS"/"7S"/"7A"/"7B"/"9S"/"9A"/"7H1"/"7C"）と "all"（既定値・全ランク合算。
          トップライン=/summaryと揃える）。
          "7SS" は 2026-08-02 に旧ランク（波乱軸選出）を全廃したあと
          2026-08-05 に別戦略（entropy不合格×同一ライン）として再設定したもの。
          旧gate_label由来の "9SS" は 2026-07-31 に廃止済みで受け付けない。
          ⚠️ "all" が含まれる、または**未知の値のみ**の場合は全体扱いに
          フォールバックする（エラーにはならない）。この仕様のせいで
          2026-08-03〜08-05 のあいだ、統計ページで "7B" を選ぶと全ランクの
          数字が表示されていた（_RANK_COND_MAP に 7B が無かったため）。
    """
    today = _today_jst()
    if to_date:
        try:
            to_dt = Date.fromisoformat(to_date)
        except ValueError:
            to_dt = today
    else:
        to_dt = today

    if from_date:
        try:
            from_dt = Date.fromisoformat(from_date)
        except ValueError:
            from_dt = today - timedelta(days=29)
    else:
        from_dt = today - timedelta(days=29)

    # 🔴 **集計は「売った商品」だけ**（2026-08-25）。以前は picks_history の
    #    `bet_amount > 0` を母集団にしていたが、あれは**ゲートを通る前の候補**にも
    #    立つ名目値で、売上にも収支にも対応しない。実測（8月）で毎日 26〜49件が
    #    「売っていないのに投資1万円・的中あり」として数えられていた。
    #    `/summary` `/sold-performance` `/picks` と同じ `_fetch_settled_submissions`
    #    を使う（別計算にすると画面ごとに違う数字が出る）。
    #
    #    ⚠️ **ランクの候補としての実力はここでは測れない**（売った分しか入らない）。
    #       候補の性能は keirin 側の walk-forward スクリプトで測ること。
    _all_labels = set(_PAPER_RANK_LABELS.values())
    _requested_keys = [k.strip() for k in rank.split(",") if k.strip()]
    if not _requested_keys or "all" in _requested_keys:
        rank_labels: list[str] | None = None
    else:
        # 未知のキーは黙って全ランクへ落とさない（7B が全ランクの数字を
        # 「7B」として出していた 2026-08-05 の事故と同型を防ぐ）。
        rank_labels = [k for k in _requested_keys if k in _all_labels] or None

    def _bucket_of(race_date: str) -> str:
        return race_date[:7] if granularity == "monthly" else race_date

    def _fold(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
        acc: dict[str, dict[str, int]] = {}
        for r in rows:
            cur = acc.setdefault(_bucket_of(str(r["race_date"])), {
                "n_picks": 0, "n_hits": 0, "n_net_hits": 0,
                "total_bet": 0, "total_payout": 0,
            })
            cur["n_picks"] += 1
            cur["n_hits"] += 1 if r["hit"] else 0
            cur["n_net_hits"] += 1 if r["net_hit"] else 0
            cur["total_bet"] += int(r["bet"])
            cur["total_payout"] += int(r["payout"])
        return acc

    sold, manual_missing = await _fetch_settled_submissions(
        db, from_dt, to_dt, rank_labels, only_missing_from_picks=False)
    stat_rows: list[dict[str, Any]] = [
        {"bucket": b, **v} for b, v in sorted(_fold(sold).items())
    ]

    # 月別・年別累積を Python 側で計算
    items: list[dict[str, Any]] = []
    cum_bet = 0
    cum_payout = 0
    month_acc: dict[str, dict[str, int]] = {}
    year_acc: dict[str, dict[str, int]] = {}

    # ウィンドウ開始日が月初/年初でない場合、cum_month/cum_year が「表示期間内の累積」に
    # なってしまいラベル（当月累計/当年累計）と乖離する。ウィンドウ前の同月・同年分を
    # 先に集計して seed し、真のカレンダー累積にする（2026-07-12）。
    if (from_dt.month, from_dt.day) != (1, 1):
        # ウィンドウ開始日が月初/年初でない場合、cum_month/cum_year が
        # 「表示期間内の累積」になりラベル（当月累計/当年累計）と乖離する。
        # ウィンドウ前の同月・同年分を先に集計して seed する（2026-07-12）。
        month_start = from_dt.replace(day=1)
        year_start = from_dt.replace(month=1, day=1)
        pre_sold, _ = await _fetch_settled_submissions(
            db, year_start, from_dt - timedelta(days=1), rank_labels,
            only_missing_from_picks=False)
        for pre in pre_sold:
            mk = str(pre["race_date"])[:7]
            yk = mk[:4]
            year_acc.setdefault(yk, {"bet": 0, "payout": 0})
            year_acc[yk]["bet"] += int(pre["bet"])
            year_acc[yk]["payout"] += int(pre["payout"])
            if mk >= month_start.strftime("%Y-%m"):
                month_acc.setdefault(mk, {"bet": 0, "payout": 0})
                month_acc[mk]["bet"] += int(pre["bet"])
                month_acc[mk]["payout"] += int(pre["payout"])

    for r in stat_rows:
        bucket = str(r["bucket"])
        n_picks = int(r["n_picks"] or 0)
        n_hits = int(r["n_hits"] or 0)
        n_net_hits = int(r["n_net_hits"] or 0)
        total_bet = int(r["total_bet"] or 0)
        total_payout = int(r["total_payout"] or 0)

        cum_bet += total_bet
        cum_payout += total_payout
        cum_roi = round(cum_payout / cum_bet, 3) if cum_bet > 0 else None

        # 月キー: YYYY-MM
        month_key = bucket[:7]
        if month_key not in month_acc:
            month_acc[month_key] = {"bet": 0, "payout": 0}
        month_acc[month_key]["bet"] += total_bet
        month_acc[month_key]["payout"] += total_payout
        m_bet = month_acc[month_key]["bet"]
        m_pay = month_acc[month_key]["payout"]
        cum_month_roi = round(m_pay / m_bet, 3) if m_bet > 0 else None

        # 年キー: YYYY
        year_key = bucket[:4]
        if year_key not in year_acc:
            year_acc[year_key] = {"bet": 0, "payout": 0}
        year_acc[year_key]["bet"] += total_bet
        year_acc[year_key]["payout"] += total_payout
        y_bet = year_acc[year_key]["bet"]
        y_pay = year_acc[year_key]["payout"]
        cum_year_roi = round(y_pay / y_bet, 3) if y_bet > 0 else None

        items.append({
            "date": bucket,
            "n_picks": n_picks,
            "n_hits": n_hits,
            # ガミ（払戻 < 賭け金）を不的中と数えたもの。**netkeirin の
            # 表示的中率はこちら**。素の的中率だけ見ると点数を増やしたとき誤読する。
            "n_net_hits": n_net_hits,
            "total_bet": total_bet,
            "total_payout": total_payout,
            "roi": round(total_payout / total_bet, 3) if total_bet > 0 else None,
            "cum_bet": cum_bet,
            "cum_payout": cum_payout,
            "cum_roi": cum_roi,
            "cum_month_roi": cum_month_roi,
            "cum_month_bet": m_bet,
            "cum_month_payout": m_pay,
            "cum_year_roi": cum_year_roi,
            "cum_year_bet": y_bet,
            "cum_year_payout": y_pay,
        })

    period_bet = cum_bet
    period_payout = cum_payout
    period_picks = sum(int(i["n_picks"]) for i in items)
    period_hits = sum(int(i["n_hits"]) for i in items)
    period_net_hits = sum(int(i["n_net_hits"]) for i in items)

    return JSONResponse(content={
        "items": items,
        "period_summary": {
            "n_picks": period_picks,
            "n_hits": period_hits,
            "n_net_hits": period_net_hits,
            "total_bet": period_bet,
            "total_payout": period_payout,
            "roi": round(period_payout / period_bet, 3) if period_bet > 0 else None,
        },
        # ⚠️ `include_manual` は 2026-08-25 から**意味を持たない**（常に売った
        #    全商品が対象）。フロントの互換のために受け取って返しているだけ。
        "include_manual": include_manual,
        # 買い目が記録されていなくて集計から外した件数（2026-08-07 以前の入稿）。
        # 黙って落とすと数字が完全に見えてしまうので必ず返す。
        "manual_missing_bet_detail": manual_missing,
    })


# netkeirin「ウマい車券」の販売有償ptに対する予想家取り分（2026-08-03・ユーザー提供）。
# 売上金額 = sold_paid_points * NETKEIRIN_REVENUE_RATE。
# 無償pt分は収益にならないため sold_points（総販売pt）ではなく
# **sold_paid_points（有償pt）** に掛けること。
#
# 🔴 正本は `services/keirin_sales_report.py`（2026-08-16 に移設）。日次の Discord
#    通知は VPS の **keirin venv** から同じ値を読むので、ここへ数値を書き戻すと
#    画面と通知で売上が食い違う。別名は後方互換のために残している。
NETKEIRIN_REVENUE_RATE = REVENUE_RATE


@router.get("/sold-performance")
async def get_sold_performance(
    from_date: str = "",
    to_date: str = "",
    group_by: str = "rank",
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """**実際に売った商品**の成績を返す（2026-08-15 新設）。

    ## picks_history との違い

    `picks_history` は**ペーパー成績**（各ランクが条件を満たした全レース）で、
    netkeirin で実際に売れるのは **1レース1商品**。母集団が違う:

      - 売っていないのに picks_history にはある（他ランクに商品を譲ったレース）
      - **売ったのに picks_history には無い**（看板の穴埋め）

    実測（2026-08-15）: 入稿472件のうち **250件（53%）に picks_history 行が無い**
    （うち233件が穴埋め）。したがって picks_history をいくら足しても
    「いくら売って、いくら返ってきたか」は出ない。

    `/stats` の `include_manual`（全入稿）は picks_history + 穴埋めの**混成**で、
    1レース1商品を守るため *売った穴埋めではなく、売っていないペーパー行を計上する*
    ことがある。ここは情報源を入稿の原本だけに固定するので、その混成が起きない。

    group_by: `rank`（既定）/ `date` / `origin`
    from_date / to_date: YYYY-MM-DD。省略時は直近30日。
    """
    today = _today_jst()
    try:
        to_dt = Date.fromisoformat(to_date) if to_date else today
    except ValueError:
        to_dt = today
    try:
        from_dt = Date.fromisoformat(from_date) if from_date else today - timedelta(days=29)
    except ValueError:
        from_dt = today - timedelta(days=29)

    rows, n_missing = await _fetch_settled_submissions(
        db, from_dt, to_dt, None, only_missing_from_picks=False)

    key = {"rank": "rank_key", "date": "race_date", "origin": "origin"}.get(
        group_by, "rank_key")

    def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(items)
        bet = sum(i["bet"] for i in items)
        pay = sum(i["payout"] for i in items)
        hits = sum(1 for i in items if i["hit"])
        net = sum(1 for i in items if i["net_hit"])
        won = sorted(i["payout"] for i in items if i["hit"])
        return {
            "n_races": n,
            "n_hits": hits,
            # 🔴 netkeirin の表示的中率はガミを不的中として数える方（excl_garami）。
            "n_net_hits": net,
            "hit_rate": round(hits / n, 4) if n else None,
            "net_hit_rate": round(net / n, 4) if n else None,
            "gami_rate": round((hits - net) / hits, 4) if hits else None,
            "total_bet": bet,
            "total_payout": pay,
            "roi": round(pay / bet, 4) if bet else None,
            "median_payout": won[len(won) // 2] if won else None,
        }

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get(key) or "—"), []).append(r)

    return JSONResponse(content={
        "from_date": from_dt.isoformat(),
        "to_date": to_dt.isoformat(),
        "group_by": group_by,
        "total": _summary(rows),
        "items": [{"key": k, **_summary(v)} for k, v in sorted(groups.items())],
        # 買い目が記録されていない入稿（bet_detail の保存は 2026-08-07 開始）。
        # 黙って落とすと「売った全部を集計した」ように見えるので必ず返す。
        "missing_bet_detail": n_missing,
    })


@router.get("/netkeirin-sales")
async def get_netkeirin_sales(
    from_date: str = "",
    to_date: str = "",
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """netkeirin「ウマい車券」二軸探偵の日別成績・売上推移を返す
    （keirin.netkeirin_sales_daily。scripts/scrape_netkeirin_sales.py が
    umaiaggre.yosoka.netkeiba.com から日次収集）。

    from_date / to_date: YYYY-MM-DD 形式。省略時は直近90日。
    通常集計はレース日の翌日に確定するため、直近1〜2日分は欠落 or 未確定値のことがある。
    """
    today = _today_jst()
    try:
        to_dt = Date.fromisoformat(to_date) if to_date else today
    except ValueError:
        to_dt = today
    try:
        from_dt = Date.fromisoformat(from_date) if from_date else today - timedelta(days=89)
    except ValueError:
        from_dt = today - timedelta(days=89)

    rows = (await db.execute(
        text("""
            SELECT
                sale_date, n_predictions, n_predictions_staked,
                n_hits_incl_garami, n_hits_excl_garami, n_miss,
                stake_amount, payout_amount, hit_rate_pct, recovery_rate_pct,
                n_sold, sold_points, sold_paid_points,
                avg_sold_points, avg_sold_minutes, avg_sold_hour
            FROM keirin.netkeirin_sales_daily
            WHERE sale_date BETWEEN :from_date AND :to_date
            ORDER BY sale_date
        """),
        {
            "from_date": from_dt.strftime("%Y%m%d"),
            "to_date": to_dt.strftime("%Y%m%d"),
        },
    )).mappings().all()

    items: list[dict[str, Any]] = []
    total_stake = total_payout = total_sold_points = total_n_sold = 0
    total_sold_paid_points = 0
    for r in rows:
        sd = str(r["sale_date"])
        stake = int(r["stake_amount"] or 0)
        payout = int(r["payout_amount"] or 0)
        total_stake += stake
        total_payout += payout
        total_sold_points += int(r["sold_points"] or 0)
        total_sold_paid_points += int(r["sold_paid_points"] or 0)
        total_n_sold += int(r["n_sold"] or 0)
        items.append({
            "date": f"{sd[0:4]}-{sd[4:6]}-{sd[6:8]}",
            "n_predictions": r["n_predictions"],
            "n_predictions_staked": r["n_predictions_staked"],
            "n_hits_incl_garami": r["n_hits_incl_garami"],
            "n_hits_excl_garami": r["n_hits_excl_garami"],
            "n_miss": r["n_miss"],
            "stake_amount": stake,
            "payout_amount": payout,
            "hit_rate_pct": r["hit_rate_pct"],
            "recovery_rate_pct": r["recovery_rate_pct"],
            "n_sold": r["n_sold"],
            "sold_points": r["sold_points"],
            "sold_paid_points": r["sold_paid_points"],
            "revenue_yen": round(int(r["sold_paid_points"] or 0) * NETKEIRIN_REVENUE_RATE),
            "avg_sold_points": r["avg_sold_points"],
            "avg_sold_minutes": r["avg_sold_minutes"],
            "avg_sold_hour": r["avg_sold_hour"],
        })

    return JSONResponse(content={
        "items": items,
        "period_summary": {
            "total_stake": total_stake,
            "total_payout": total_payout,
            "recovery_rate_pct": round(total_payout / total_stake * 100, 1) if total_stake > 0 else None,
            "total_sold_points": total_sold_points,
            "total_sold_paid_points": total_sold_paid_points,
            "total_n_sold": total_n_sold,
            "revenue_rate": NETKEIRIN_REVENUE_RATE,
            "total_revenue_yen": round(total_sold_paid_points * NETKEIRIN_REVENUE_RATE),
        },
    })


@router.get("/netkeirin-analysis")
async def get_netkeirin_analysis(
    from_date: str = "",
    to_date: str = "",
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """netkeirin の売上 × 成績の相関分析（成績／売上ページの「分析」タブ）。

    一次資料は2つのテーブル:
      - keirin.netkeirin_sales_daily … 日別（相関・ガミ推移・前日比）
      - keirin.netkeirin_sales_race  … レース別（どのレースが売れた／当たった）
    どちらも scripts/scrape_netkeirin_sales.py が毎日10:30に前日分を UPSERT する。

    レース別には kiseki 側の情報を結合して返す:
      - keirin.wt_races              … 開催の第1R発走時刻 → 開催時間帯
                                       （判定は keirin_meeting.py が正本）
      - keirin.netkeirin_submissions … 入稿ランクと**出自**（netkeirin 側には無い断面）

    🔴 **ランク別だけ見ても経路は分からない。** 看板レースの穴埋め入稿は
       `RANK_BY_CARS={7:"7A",9:"9A"}` により 7A/9A を名乗るため、
       `rank_key` にはゲート通過分と穴埋めが混ざる（実測で 7A の94%が穴埋め）。
       `origin`（migration 202608111930_keirin）で割ること。
    どちらも **LEFT JOIN**。結合できないレース（入稿記録が無い・出走表未取得）を
    落とすと売上の合計が netkeirin の実績と合わなくなる。

    ⚠️ ランクの結合キーは `netkeirin_submissions.netkeirin_race_id`（netkeirin の
       12桁レースID そのもの）を使う。**`picks_history.race_key` は使えない**
       ―― あちらのキーは `20260801_13_05#7C` のようにランク接尾辞が付いており、
       1レースが複数ランクで並ぶ（＝どれを入稿したかが決まらない）。

    from_date / to_date: YYYY-MM-DD。省略時は直近30日。
    """
    today = _today_jst()
    try:
        to_dt = Date.fromisoformat(to_date) if to_date else today
    except ValueError:
        to_dt = today
    try:
        from_dt = Date.fromisoformat(from_date) if from_date else today - timedelta(days=29)
    except ValueError:
        from_dt = today - timedelta(days=29)
    params = {
        "from_date": from_dt.strftime("%Y%m%d"), "to_date": to_dt.strftime("%Y%m%d"),
        # picks_history.race_date は 'YYYY-MM-DD'（他と書式が違う）
        "from_iso": from_dt.isoformat(), "to_iso": to_dt.isoformat(),
    }

    daily_rows = (await db.execute(
        text("""
            SELECT sale_date, n_predictions, n_hits_incl_garami, n_hits_excl_garami,
                   stake_amount, payout_amount, n_sold, sold_points, sold_paid_points
            FROM keirin.netkeirin_sales_daily
            WHERE sale_date BETWEEN :from_date AND :to_date
            ORDER BY sale_date
        """),
        params,
    )).mappings().all()

    # 開催（日×会場）の第1R発走時刻。wt_races.start_at は UNIX 秒の文字列なので
    # 最小値＝第1R。ここでは時刻だけ取り、種別への変換は keirin_meeting.py に任せる。
    race_rows = (await db.execute(
        text("""
            WITH meeting_first AS (
                SELECT replace(race_date, '-', '') AS ymd,
                       venue_id,
                       MIN(start_at::bigint) AS first_start_at
                FROM keirin.wt_races
                WHERE replace(race_date, '-', '') BETWEEN :from_date AND :to_date
                  AND start_at IS NOT NULL AND start_at <> ''
                GROUP BY 1, 2
            ),
            -- 入稿ランク。PK は (race_key, rank_key) なので理屈の上では1レース複数行に
            -- なりうる。DISTINCT ON で「生きている入稿を優先し、同じなら新しい方」に畳む。
            submission AS (
                SELECT DISTINCT ON (netkeirin_race_id)
                       netkeirin_race_id, rank_key, origin, deleted_at
                FROM keirin.netkeirin_submissions
                WHERE netkeirin_race_id IS NOT NULL
                ORDER BY netkeirin_race_id,
                         (deleted_at IS NULL) DESC, submitted_at DESC
            ),
            -- そのレースに **何らかの** ランク候補が立っていたか（ランク名は問わない）。
            -- 🔴 `picks_history.race_key` は `20260801_13_05#7C` とランク接尾辞つきなので
            --    接尾辞を落として突き合わせる。**ランク名で等値結合してはいけない**
            --    ―― 看板の穴埋めは 7A/9A を名乗るため、7C 候補のレースを 7A で
            --    入稿した分が「候補なし」に見えてしまう（2026-08-11 に実際に誤読した）。
            candidate AS (
                SELECT split_part(race_key, '#', 1) AS base_key,
                       string_agg(DISTINCT replace(rank, 'RANK_', ''), ',' ORDER BY replace(rank, 'RANK_', '')) AS detected_ranks
                FROM keirin.picks_history
                WHERE race_date BETWEEN :from_iso AND :to_iso
                GROUP BY 1
            )
            SELECT s.race_id, s.race_key, s.race_date, s.venue_code, s.race_no, s.race_label,
                   s.n_hits_incl_garami, s.n_hits_excl_garami,
                   s.stake_amount, s.payout_amount,
                   s.n_sold, s.sold_points, s.sold_paid_points,
                   s.avg_sold_minutes, s.avg_sold_hour,
                   v.name AS venue_name,
                   sub.rank_key AS rank,
                   sub.origin AS origin,
                   c.detected_ranks,
                   m.first_start_at
            FROM keirin.netkeirin_sales_race s
            LEFT JOIN keirin.venue_info v ON v.venue_code = s.venue_code
            LEFT JOIN submission sub ON sub.netkeirin_race_id = s.race_id
            LEFT JOIN meeting_first m ON m.ymd = s.race_date AND m.venue_id = s.venue_code
            LEFT JOIN candidate c ON c.base_key = s.race_key
            WHERE s.race_date BETWEEN :from_date AND :to_date
            ORDER BY s.race_date, s.race_id
        """),
        params,
    )).mappings().all()

    races_src = [
        {**dict(r), "meeting_type": meeting_type_of_first_hour(first_hour_jst(r["first_start_at"]))}
        for r in race_rows
    ]

    daily = build_daily([dict(r) for r in daily_rows])
    races = build_races(races_src)

    return JSONResponse(content={
        "from_date": from_dt.isoformat(),
        "to_date": to_dt.isoformat(),
        "summary": build_summary(daily, races),
        "daily": daily,
        "races": races,
        "correlations": build_correlations(daily, races),
        "link_check": build_link_check(daily),
        "leadtime": build_leadtime_buckets(races),
        "by_rank": build_rank_breakdown(races),
        "by_origin": build_origin_breakdown(races),
        "by_route": build_route_breakdown(races),
        "revenue_rate": NETKEIRIN_REVENUE_RATE,
    })


#: **実販売の開始日**。この日より前は netkeirin へ出しておらず（or 買い目の原本
#: `bet_detail` が残っていない）、実売として集計できない。
#:
#: 🔴 サマリーの期間行は **この日を境に出所を切り替える**（2026-08-21・ユーザー判断
#: 「2026-07まで実販売前なので現在の構成でペーパーとして埋めて」）:
#:
#:     race_date <  REAL_SALES_FROM  … `picks_history`（ペーパー・現行ランクのみ）
#:     race_date >= REAL_SALES_FROM  … `netkeirin_submissions`（実際に売った商品）
#:
#: ⚠️ **入稿1件ずつを紙で代用するのは不可能**なので日付で切っている。
#:    実測（2026-08-21）: `bet_detail` の無い入稿 185件のうち `picks_history` に
#:    対応行があるのは **33件（18%）だけ**。しかも `marquee_fill`（147件）は
#:    `submit_marquee_wt.py` が独自に組む別商品で、紙の行は**別の買い目**を
#:    記録している（実例 `20260727_28_03`: 入稿の軸2は 1 だが紙の 7C は 4）。
REAL_SALES_FROM = "2026-08-07"

#: ペーパー通算の起点。`picks_history` はここから連続している
#: （2024-01 より前は月次 vintage が無く再構築できない＝`wt_vintage_config.FIRST_MONTH`）。
PAPER_TOTAL_SINCE = "2024-01-01"


async def _paper_slice(
    db: AsyncSession, since: str, until: str,
) -> dict[str, int]:
    """`since`〜`until`（両端含む）のペーパー集計。現行ランクのみ。

    サマリーの期間行が **実販売開始前**を埋めるために使う（`REAL_SALES_FROM`）。
    """
    r = (await db.execute(
        text(f"""
            SELECT COUNT(*)                                    AS n_picks,
                   COUNT(*) FILTER (WHERE ph.payout > 0)       AS n_hits,
                   COALESCE(SUM(ph.bet_amount), 0)             AS total_bet,
                   COALESCE(SUM(ph.payout), 0)                 AS total_payout,
                   MAX(ph.payout) FILTER (WHERE ph.payout > 0) AS max_payout
            FROM keirin.picks_history ph
            WHERE ph.race_date BETWEEN :s AND :u
              AND ph.bet_amount > 0
              AND ph.route = 'wt'
              AND ph.rank IN {_RANKS_ALL}
              AND {_enabled_rank_cond()}
        """),
        {"s": since, "u": until},
    )).mappings().first()
    d: dict[str, Any] = dict(r) if r is not None else {}
    return {
        "n_picks": int(d.get("n_picks") or 0),
        "n_hits": int(d.get("n_hits") or 0),
        "total_bet": int(d.get("total_bet") or 0),
        "total_payout": int(d.get("total_payout") or 0),
        "max_payout": int(d["max_payout"]) if d.get("max_payout") else 0,
    }


_EMPTY_PAPER: dict[str, int] = {
    "n_picks": 0, "n_hits": 0, "total_bet": 0, "total_payout": 0, "max_payout": 0,
}


async def _paper_for_period(
    db: AsyncSession, from_dt: Date, to_dt: Date,
) -> dict[str, int]:
    """期間 `from_dt`〜`to_dt` のうち、**実販売開始前に重なる部分**のペーパー集計。

    🔴 当年だけでなく **当日・当月にも効かせる**（2026-08-22・ユーザー要望
       「ペーパー表示は日時で確認できませんか？」）。日付ナビで実販売開始前へ
       遡ったとき、そのままだと実売が無いので全部 0 になり、
       **その日に何を推奨していたかが画面から消える**。

    重なりが無ければ 0 の辞書を返す（合算しても何も変わらない）。
    """
    end = Date.fromisoformat(REAL_SALES_FROM) - timedelta(days=1)
    lo, hi = from_dt, min(to_dt, end)
    if lo > hi:
        return dict(_EMPTY_PAPER)
    return await _paper_slice(db, lo.isoformat(), hi.isoformat())


def _merge_paper_into(period: dict, paper: dict[str, int]) -> dict:
    """実売の期間集計へ**実販売開始前のペーパー分**を合算する。

    🔴 **合算した事実は値として必ず残す**（`paper_picks` / `paper_from` / `paper_to`）。

    ⚠️ **2026-08-24 に画面からペーパーの表記を全て外した**（ユーザー判断）。
       以前は「黙って足すと『当年＝全部実売』と読まれる」ので注記で区別していたが、
       **実売のマスターは netkeirin 側**であり、この画面は
       **「モデルが推奨した場合のサンプル確認」がベース**という位置づけになった。
       ＝ そもそも実売成績として読む画面ではないので注記が要らない。
       🔴 **値は返し続ける**（`paper_picks`）。表示を戻すときに
          `frontend/src/app/keirin/page.tsx` のブロックを復活させるだけで済む。
    """
    if paper["n_picks"] == 0:
        return period
    period["n_picks"] += paper["n_picks"]
    period["n_hits"] += paper["n_hits"]
    period["total_bet"] += paper["total_bet"]
    period["total_payout"] += paper["total_payout"]
    if paper["max_payout"]:
        period["max_payout"] = max(period.get("max_payout") or 0,
                                   paper["max_payout"])
    period["roi"] = (round(period["total_payout"] / period["total_bet"], 3)
                     if period["total_bet"] > 0 else None)
    period["paper_picks"] = paper["n_picks"]
    return period


async def _aggregate_paper(
    db: AsyncSession, since: str = PAPER_TOTAL_SINCE,
) -> dict:
    """**ペーパー**（`picks_history`）の通算。現行ランクだけに絞る。

    🔴 **`_aggregate` とは母集団が違う。同じ表に無印で並べてはいけない。**
       `_aggregate` の投資・払戻は `netkeirin_submissions`＝**実際に売った商品**から
       数えるが、その原本は **2026-07-24 開始・842件**しかない。
       一方 `picks_history` は 2024-01 から 37,708件あるが、これは
       「もし買っていたら」の紙の記録である。
       混ぜると `/review`(実売) と `/keirin`(ペーパー) の不一致を「不具合」と
       誤診する型に戻る（memory: keirin_display_reality_drift_2026_08_16）。
       → フロントは **必ず「ペーパー」と明示して**描くこと。

    🔴 **現行ランクだけに絞る**（ユーザー判断 2026-08-21・案C）。
       2024→2026 でランク数は 7 → 8 → 12 と変わり、廃止済みの
       7A / 7SS / 9A / 9S が 2024〜2025 に大量に入っている。
       全部混ぜた「2024年 ROI 78.8%」は**いま売っていない商品を含む数字**で、
       過去傾向の参考にならない。`_enabled_rank_cond()` で入稿ONのものだけ見る。

    ⚠️ **それでも `rule_version` の変化までは吸収できない。**
       同じランクでも買い方は何度も変わっている（7M1 は 2026-08-21 に相手選択を
       EV順へ変更）。**世代をまたいだ通算であることを承知で読むこと。**
    """
    row_raw = (await db.execute(
        text(f"""
            SELECT COUNT(*)                                   AS n_picks,
                   COUNT(*) FILTER (WHERE ph.payout > 0)      AS n_hits,
                   COALESCE(SUM(ph.bet_amount), 0)            AS total_bet,
                   COALESCE(SUM(ph.payout), 0)                AS total_payout,
                   MAX(ph.payout) FILTER (WHERE ph.payout > 0) AS max_payout
            FROM keirin.picks_history ph
            WHERE ph.race_date >= :since
              AND ph.bet_amount > 0
              AND ph.route = 'wt'
              AND ph.rank IN {_RANKS_ALL}
              AND {_enabled_rank_cond()}
        """),
        {"since": since},
    )).mappings().first()
    row: dict[str, Any] = dict(row_raw) if row_raw is not None else {}

    result = _make_period_dict(
        int(row.get("n_picks") or 0), int(row.get("n_hits") or 0),
        int(row.get("total_bet") or 0), int(row.get("total_payout") or 0),
        int(row["max_payout"]) if row.get("max_payout") else None)
    result["since"] = since
    result["is_paper"] = True     # フロントがラベルを出すための印

    by_rank: dict[str, dict] = {}
    rank_rows = (await db.execute(
        text(f"""
            SELECT ph.rank AS rank,
                   COUNT(*)                                   AS n_picks,
                   COUNT(*) FILTER (WHERE ph.payout > 0)      AS n_hits,
                   COALESCE(SUM(ph.bet_amount), 0)            AS total_bet,
                   COALESCE(SUM(ph.payout), 0)                AS total_payout,
                   MAX(ph.payout) FILTER (WHERE ph.payout > 0) AS max_payout
            FROM keirin.picks_history ph
            WHERE ph.race_date >= :since
              AND ph.bet_amount > 0
              AND ph.route = 'wt'
              AND ph.rank IN {_RANKS_ALL}
              AND {_enabled_rank_cond()}
            GROUP BY ph.rank
        """),
        {"since": since},
    )).mappings().all()
    for r in rank_rows:
        by_rank[_display_rank(str(r["rank"]))] = _make_period_dict(
            int(r["n_picks"] or 0), int(r["n_hits"] or 0),
            int(r["total_bet"] or 0), int(r["total_payout"] or 0),
            int(r["max_payout"]) if r["max_payout"] else None)
    result["by_rank"] = by_rank
    return result


@router.get("/summary")
async def get_summary(date: str = "", db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """当日 / 当月 / 当年のサマリーを返す。
    date（YYYY-MM-DD）を指定するとその日付を基準に当日/当月/当年を集計する。
    """
    try:
        today = Date.fromisoformat(date) if date else _today_jst()
    except ValueError:
        today = _today_jst()
    today_str = today.isoformat()
    month_prefix = today.strftime("%Y-%m")
    year_prefix = str(today.year)

    # 2026-07-27〜: today/month/year は既定(rank_filter=_RANKS_ALL)でS1+S7+S9+7A+9Aを
    # まとめて集計する。by_rank（_aggregate内部で_display_rank()により算出）には
    # S1/7SS/7S/7A（7車）と9SS/9S/9A（9車）が同じ辞書に並ぶため、
    # フロントエンドの「ランク別」展開でまとめて確認できる（7A/9Aを専用の別集計に
    # 分離していたが、表示が煩雑とのユーザー要望により同日中に統合した）。
    # 🔴 **4つの塊を並行に走らせる**（2026-08-23）。
    #    以前は dict リテラルの中で8本を `await` しており、**Python が逐次に
    #    評価する**ため全部足し算になっていた（本番実測 合計 2,216ms:
    #    year 722 / month 655 / today 410 / paper_total 217 / 他）。
    #    2026-08-21 にペーパー補完が3期間へ増えてから、この直列が
    #    そのまま `/keirin` の表示時間になっていた。
    #
    # ⚠️ **同じ `AsyncSession` を並行に使ってはいけない**（SQLAlchemy は
    #    セッションの同時使用を許さず `another operation is in progress` になる）。
    #    塊ごとに**別セッション**を張る。リクエスト自身の `db` は1つ目に使い、
    #    追加の接続は3本に抑える（プールは pool_size 5 + max_overflow 15）。
    # ⚠️ 塊を細かく割りすぎないこと。接続本数が増えるだけで、律速は
    #    いちばん重い `year` の集計なので 4分割より先は縮まない。
    async def _period(session: AsyncSession, cond: str, params: dict,
                      from_dt: Date, to_dt: Date) -> dict:
        # 🔴 投資・払戻・的中は**実際に売った商品**から数える（`_aggregate` の
        #    docstring 参照）。期間は SQL 条件（候補数用）と日付（実売用）の
        #    両方を渡す —— 片方だけにすると母集団がずれる。
        # 🔴 3期間とも **実販売開始前に重なる部分はペーパーで埋める**
        #    （`_paper_for_period`）。当年だけに効かせていた頃は、日付ナビで
        #    実販売開始前へ遡ると当日・当月が全部 0 になり、その日に何を推奨して
        #    いたかが画面から消えていた（2026-08-22 是正）。
        return _merge_paper_into(
            await _aggregate(session, cond, params, from_dt=from_dt, to_dt=to_dt),
            await _paper_for_period(session, from_dt, to_dt))

    async def _in_new_session(fn):
        async with AsyncSessionLocal() as s:
            return await fn(s)

    async def _totals(session: AsyncSession) -> tuple[dict, list]:
        return (await _aggregate_paper(session),
                await visible_rank_labels(session))

    r_today, r_month, r_year, (paper_total, visible_ranks) = await asyncio.gather(
        _period(db, "ph.race_date = :d", {"d": today_str}, today, today),
        _in_new_session(lambda s: _period(
            s, "ph.race_date LIKE :d", {"d": f"{month_prefix}-%"},
            today.replace(day=1), today)),
        _in_new_session(lambda s: _period(
            s, "ph.race_date LIKE :d", {"d": f"{year_prefix}-%"},
            Date(today.year, 1, 1), today)),
        _in_new_session(_totals),
    )

    result = {
        "today": r_today,
        "month": r_month,
        "year": r_year,
        # フロントの「ランク別」展開・絞り込みチップはこの一覧で絞る。
        # 集計側（_aggregate）は既に入稿OFFを除外しているので by_rank には
        # 現れないが、**チップは行が0件でも描かれる**ので明示的に渡す。
        # ペーパー通算（2026-08-21 追加）。上3行とは母集団が違う
        # （上=実際に売った商品・2026-07-24〜 / これ=picks_history・2024-01〜）。
        # ⚠️ **2026-08-24 に画面から外した**（ユーザー判断・上記 `_merge_paper_into`）。
        #    値は返し続ける——集計は並列化済みで 217ms（律速は year の 722ms）なので
        #    外しても速くならず、残しておけば表示を戻すのが1ブロックで済む。
        "paper_total": paper_total,
        "visible_ranks": visible_ranks,
    }

    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# netkeirin（ウマい車券）自動入稿設定
# ---------------------------------------------------------------------------

# 表示ランク一覧（_display_rank()の出力と一致）。並び順は _PAPER_RANK_LABELS の
# 定義順＝Web 全体の表示順（frontend の RANK_ORDER / RANK_FILTERS と同一基準）。
# '_global' は全体ON/OFFを表す特殊行。
#
# 【2026-08-05 是正】ここも手書きのタプルで、2026-08-03 新設の 7B が抜けたまま
# だった（設定画面から 7B の入稿ON/OFF・文面を保存しようとすると 400 になる）。
# _PAPER_RANK_LABELS から導出してランク名の二重管理をやめる。
# DBには過去分の行（rank_key='S1'/'9SS' 等・enabled=false）が残るが、新規保存時の
# バリデーション対象からは自動的に外れる（フロントも画面に表示しない）。
NETKEIRIN_RANK_KEYS = ("_global", *_PAPER_RANK_LABELS.values())


class NetkeirinSettingOut(BaseModel):
    rank_key: str
    enabled: bool
    title_template: str
    comment_template: str


class NetkeirinSettingIn(BaseModel):
    rank_key: str
    enabled: bool
    title_template: str
    comment_template: str


@router.get("/netkeirin-settings")
async def get_netkeirin_settings(db: AsyncSession = Depends(get_db)) -> list[NetkeirinSettingOut]:
    """netkeirin自動入稿のランク別ON/OFF・タイトル/コメントテンプレート一覧を返す。"""
    rows = (await db.execute(select(KeirinNetkeirinSetting))).scalars().all()
    return [
        NetkeirinSettingOut(
            rank_key=r.rank_key,
            enabled=r.enabled,
            title_template=r.title_template,
            comment_template=r.comment_template,
        )
        for r in rows
    ]


@router.put("/netkeirin-settings")
async def update_netkeirin_settings(
    body: list[NetkeirinSettingIn],
    _: ApiKeyDep,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """netkeirin自動入稿設定を一括更新する（upsert・rank_keyはallowlist検証）。"""
    for item in body:
        if item.rank_key not in NETKEIRIN_RANK_KEYS:
            return JSONResponse(
                content={"ok": False, "message": f"不正なrank_key: {item.rank_key}"},
                status_code=400,
            )
    for item in body:
        stmt = (
            pg_insert(KeirinNetkeirinSetting)
            .values(
                rank_key=item.rank_key,
                enabled=item.enabled,
                title_template=item.title_template,
                comment_template=item.comment_template,
            )
            .on_conflict_do_update(
                index_elements=["rank_key"],
                set_={
                    "enabled": item.enabled,
                    "title_template": item.title_template,
                    "comment_template": item.comment_template,
                    "updated_at": func.now(),
                },
            )
        )
        await db.execute(stmt)
    await db.commit()
    return JSONResponse(content={"ok": True, "updated": len(body)})


# ---------------------------------------------------------------------------
# 入稿案の確認・承認（2026-08-11）
# ---------------------------------------------------------------------------
# 朝の入稿前に「オッズ・推奨買い目・コメント」を確認したい、という要望で
# netkeirin 入稿へ承認制を入れた。keirin 側がバッチで **入稿案**（netkeirin へは
# 送らない行・status='proposed'）を作り、ここがそれを画面へ渡す。
#
# 読み取りは keirin スキーマを**直接**見る（webhook を経由しない）。
# 書き込み（承認・取消）だけは netkeirin のセッションが要るので webhook 経由。
STATUS_PROPOSED = "proposed"
STATUS_SUBMITTED = "submitted"
logger = logging.getLogger(__name__)

STATUS_DELETED = "deleted"

#: レース信頼度指標のための履歴集計。**当該レースより前**だけを数える
#: （当日を含めると結果を見て予想することになる）。
# 🔴 **`wt_races` と結合してはいけない**（2026-08-23・本番を遅くした実バグ）。
#    `race_key` は `YYYYMMDD_場_R` 形式なので `r.race_date < :d` は
#    **`e.race_key < :ymd`（8桁の日付文字列）と等価**。結合を外すだけで
#    本番実測 2,576ms → 655ms になった（`wt_races` 102,221行の seq scan が消える）。
#    さらに `ix_wt_entries_player_id`（202608230945_keirin）で index only scan になる。
#
#    文字列比較で正しく効く理由: `'20260822_27_01' < '20260823'` は7文字目まで一致し
#    8文字目 `'2' < '3'` で真。同日の `'20260823_27_01' < '20260823'` は
#    8文字まで一致したうえで**長い方が大きい**ので偽。＝ 前日以前だけが残る。
Q_SQL_CRASH = """
    WITH ent AS (
      SELECT race_key, player_id FROM keirin.wt_entries
      WHERE race_key = ANY(:keys) AND player_id IS NOT NULL
    ), h AS (
      SELECT e.player_id,
             count(*) AS starts,
             count(*) FILTER (WHERE COALESCE(e.finish_order, 0) < 1) AS dnf
      FROM keirin.wt_entries e
      WHERE e.player_id IN (SELECT player_id FROM ent)
        AND e.race_key < :ymd
      GROUP BY 1
    )
    SELECT ent.race_key, COALESCE(h.starts, 0) AS starts, COALESCE(h.dnf, 0) AS dnf
    FROM ent LEFT JOIN h ON h.player_id = ent.player_id
"""


# 公開済み（2026-08-16）。netkeirin は「入稿（下書きとして送る）」と「公開」が
# 別操作で、公開すると修正できなくなる（不可逆）。
#   proposed → submitted（＝公開待ち）→ published    ↘ deleted
STATUS_PUBLISHED = "published"


def _payout_range(lines: list[dict]) -> tuple[float | None, float | None]:
    """買い目の **最低払戻 / 最高払戻**（円）。オッズが1つでも欠けたら None。

    🔴 一部だけで計算してはいけない。欠けた点が最安だった場合に
       「最低払戻」を実際より高く見せることになり、確認の役に立たない。
    """
    if not lines or any(x.get("odds") in (None, 0) for x in lines):
        return None, None
    rets = [float(x["stake"]) * float(x["odds"]) for x in lines]
    return min(rets), max(rets)


#: 想定払戻の**平均**がこの額以下なら、レビュー画面で一括取消の候補にする（円）。
#
# 🔴 **正本は `keirin/src/stake_allocation.py::MIN_MEAN_PAYOUT`**。ここはAPIが
#    候補を選ぶための写しで、`keirin/tests/test_min_mean_payout_gate.py` が
#    食い違いを機械的に落とす（`SUBMIT_DEADLINE_SEC` と同じ作法）。
# 🔴 **自動では落とさない。** ユーザー方針（2026-08-24）は「入稿はいったん通し、
#    レビュー画面から人が確認して一括取消する」。ここは目印を返すだけ。
CHEAP_MEAN_PAYOUT = 20_000


def _mean_payout(lines: list[dict]) -> float | None:
    """買い目の想定払戻の**平均**（円）。オッズが1つでも欠けたら None。

    🔴 `_payout_range` と同じ理由で**一部だけで計算しない**。欠けた点が最安
       だった場合に平均を実際より高く見せ、取消候補から漏れる。
    ⚠️ `min_payout`（最低）とは別の量。ダッチング配分では近いが、均等配分の
       経路（三連単・旧候補）では大きく開く。
    """
    if not lines or any(x.get("odds") in (None, 0) for x in lines):
        return None
    rets = [float(x["stake"]) * float(x["odds"]) for x in lines]
    return sum(rets) / len(rets)


def _min_payout_low(lines: list[dict]) -> float | None:
    """**下振れしても割らない**最低払戻（円）。`odds_low` が全点に無ければ None。

    ## なぜ `odds` と別に要るのか（2026-08-16・実測が起点）

    `odds` は入稿時点の板が最優先で、**朝の板は買い目の帯で確定までに大きく下がる**。
    実入稿 705点を確定オッズと突合した結果:

    | `odds` の出どころ | 中央 確定/表示 | <0.8倍 |
    |---|---|---|
    | 板 | **0.860** | **45.0%** |
    | 予測（構造モデル） | 1.181 | 16.7% |

    つまり従来の `min_payout` は**当たったときに実際より高い額を約束していた**。
    `odds_low` は keirin 側 `_conservative_trio_board()` が作る下限包絡
    （予測の整合板 × 学習窓較正の下側25%分位）で、これで測ると
    「利益が出ると言った点が実はガミ」は 10.4% → 5.1% に下がる。

    🔴 **`odds_low` はオッズではない。** 表示は従来どおり `odds` を出し、
       ここで作るのは「下振れ時にいくら返るか」という金額だけ。
    ⚠️ 三連単は予測モデルが無いので `odds_low` が付かない＝ None になる
       （その場合は従来どおり `min_payout` で判断する）。
    """
    if not lines or any(x.get("odds_low") in (None, 0) for x in lines):
        return None
    return min(float(x["stake"]) * float(x["odds_low"]) for x in lines)


def _trio_probabilities(top3_pct: dict[int, float]) -> dict[frozenset, float]:
    """3着内率から三連複の各目の確率をつくる（レース内で正規化）。

    keirin `src/odds_prediction.py` の PROD と同じ作り方。厳密な同時確率では
    ないが、レース内の相対比較には足りる。
    """
    cars = [c for c, v in top3_pct.items() if v and v > 0]
    if len(cars) < 3:
        return {}
    raw: dict[frozenset, float] = {}
    for i in range(len(cars)):
        for j in range(i + 1, len(cars)):
            for k in range(j + 1, len(cars)):
                a, b, c = cars[i], cars[j], cars[k]
                raw[frozenset((a, b, c))] = (
                    top3_pct[a] / 100 * top3_pct[b] / 100 * top3_pct[c] / 100)
    z = sum(raw.values())
    return {k: v / z for k, v in raw.items()} if z > 0 else {}


def _expected_value(lines: list[dict], top3_pct: dict[int, float]) -> float | None:
    """買い目全体の期待値（回収率の見込み・1.0 で収支トントン）。

    ⚠️ **購入判断の根拠に使ってはいけない。** 競輪の市場は効率的で、モデル由来の
       期待値による選別は繰り返し否定されている（keirin_gami_race_gate_rejected /
       keirin_trifecta_ev_closed）。ここで出すのは**異常値の検知**が目的。
       実用上は「最低払戻がガミ域に入っていないか」を見るほうが確実。
    """
    if not lines or any(x.get("odds") in (None, 0) for x in lines):
        return None
    probs = _trio_probabilities(top3_pct)
    if not probs:
        return None
    total = sum(float(x["stake"]) for x in lines)
    if total <= 0:
        return None
    ev = 0.0
    for x in lines:
        # 三連複の combo は "1=2=5"。三連単（"-" 区切り）は着順があり
        # ここの確率モデルでは扱えないので None を返す。
        if "-" in str(x["combo"]):
            return None
        try:
            cars = frozenset(int(c) for c in str(x["combo"]).split("="))
        except ValueError:
            return None
        p = probs.get(cars)
        if p is None:
            return None
        ev += p * float(x["stake"]) * float(x["odds"])
    return ev / total


@router.get("/proposals/count")
async def get_proposals_count(date: str = "",
                              db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """未承認の入稿案の**件数だけ**を返す（バッジ用・2026-08-23 新設）。

    🔴 **トップページは `/proposals` を呼んではいけない。** あちらは全レースの
       買い目・出走表・落車リスクを含み **201〜282KB / 約3秒**かかるが、
       トップが欲しいのは `n_proposed`（整数ひとつ）だけだった。
       本番実測でページ表示の支配項になっていた（2026-08-21〜）。

    こちらは `netkeirin_submissions` を1本引くだけで、他の表には触らない。
    """
    target = date or _today_jst().isoformat()
    if not _DATE_RE.match(target):
        return JSONResponse(content={"ok": False, "message": f"不正な日付: {target}"},
                            status_code=400)
    ymd = target.replace("-", "")
    # 🔴 **`deleted_at IS NULL` を足してはいけない。** `/proposals` 側の
    #    `n_proposed` は削除済みの行も数えている（`WHERE race_key LIKE :pat` だけで
    #    絞り、items をそのまま数える）。ここで条件を足すと**バッジの数字が変わる**。
    #    挙動を変えるかどうかは性能修正とは別の判断なので、まず一致させる。
    n = (await db.execute(text("""
        SELECT count(*) FROM keirin.netkeirin_submissions
        WHERE race_key LIKE :pat AND status = :st
    """), {"pat": f"{ymd}%", "st": STATUS_PROPOSED})).scalar_one()
    return JSONResponse(content={"date": target, "n_proposed": int(n or 0)})


@router.get("/proposals")
async def get_proposals(date: str = "", db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """指定日の入稿案・入稿済みを、確認画面が必要とする情報つきで返す。

    返すもの:
      - 入稿内容（タイトル・コメント・買い目と金額配分）
      - 期待値・最低払戻・最高払戻
      - 選手ごとの各入着率（1着率・3着内率）と WT印・競走得点・ライン
    """
    target = date or _today_jst().isoformat()
    if not _DATE_RE.match(target):
        return JSONResponse(content={"ok": False, "message": f"不正な日付: {target}"},
                            status_code=400)
    ymd = target.replace("-", "")

    rows = (await db.execute(text("""
        SELECT s.race_key, s.rank_key, s.origin, s.status, s.session, s.venue_name, s.race_no,
               s.axis1, s.axis2, s.title, s.comment, s.bet_detail, s.is_confident, s.confident_ev,
               s.netkeirin_race_id, s.proposed_at, s.approved_at, s.deleted_at,
               s.cancel_reason,
               r.start_at, r.grade, r.race_type, r.n_entries, r.cup_grade
        FROM keirin.netkeirin_submissions s
        LEFT JOIN keirin.wt_races r ON r.race_key = s.race_key
        WHERE s.race_key LIKE :pat
        ORDER BY r.start_at NULLS LAST, s.venue_name, s.race_no
    """), {"pat": f"{ymd}%"})).mappings().all()
    if not rows:
        return JSONResponse(content={"date": target, "items": []})

    keys = sorted({r["race_key"] for r in rows})
    ent_rows = (await db.execute(text("""
        SELECT race_key, frame_no, name, race_point, style, line_group, line_pos,
               player_class, prediction_mark, pred_win_pct, pred_top2_pct, pred_top3_pct,
               finish_order
        FROM keirin.wt_entries WHERE race_key = ANY(:keys) ORDER BY frame_no
    """), {"keys": keys})).mappings().all()
    by_race: dict[str, list[dict]] = {}
    for e in ent_rows:
        by_race.setdefault(e["race_key"], []).append(dict(e))

    # --- レース信頼度指標（落車リスク・2026-08-21 新設）------------------
    # 出走者の落車性向（そのレースより前の実績のみ・経験ベイズ縮約）の平均。
    # 🔴 **判断材料であってゲートではない。** 危険帯を自動で落とすと、実測で
    #    最も ROI の高い四分位（Q4 78.1%）を捨てることになる。詳細は
    #    `services/keirin_crash_risk.py` の docstring。
    risk_by_race: dict[str, float] = {}
    try:
        # :ymd は上で作った `target.replace("-", "")`＝`race_key` の前置8桁と
        # 比べるための文字列（Q_SQL_CRASH のコメント参照）。
        hist = (await db.execute(text(Q_SQL_CRASH),
                                 {"keys": keys, "ymd": ymd})).mappings().all()
        riders: dict[str, list[tuple[int, int]]] = {}
        for x in hist:
            riders.setdefault(x["race_key"], []).append(
                (int(x["starts"]), int(x["dnf"])))
        for rk_, rs in riders.items():
            v = race_risk(rs)
            if v is not None:
                risk_by_race[rk_] = v
    except Exception as e:  # pragma: no cover - 付随情報なので画面を落とさない
        logger.warning("[keirin] 信頼度指標の算出に失敗（表示のみスキップ）: %s", e)

    items: list[dict[str, Any]] = []
    for r in rows:
        detail = _parse_bet_detail(r["bet_detail"])
        lines = detail["lines"] if detail else []
        entries = by_race.get(r["race_key"], [])
        top3 = {int(e["frame_no"]): float(e["pred_top3_pct"])
                for e in entries if e["pred_top3_pct"] is not None}
        lo, hi = _payout_range(lines)
        lo_low = _min_payout_low(lines)
        mean_pay = _mean_payout(lines)
        items.append({
            "race_key": r["race_key"],
            "rank_key": r["rank_key"],
            # 入稿の出自。承認者が「これはゲートを通っていない商品」と分かるように
            # 返す（穴埋めは 7A/9A を名乗るため rank_key では区別できない）。
            "origin": r["origin"] or ORIGIN_RANK,
            "status": r["status"] or STATUS_SUBMITTED,
            "session": r["session"],
            "venue_name": r["venue_name"],
            "race_no": r["race_no"],
            "grade": r["grade"],
            "race_type": r["race_type"],
            "is_marquee": is_marquee_race(r["race_type"]),
            # レース信頼度（0〜100%）。**ランクのゲートが見ているのと同じ量**を
            # 100% ＝ 上位2車の3着内率合計 2.00 として百分率にしたもの。
            # 判定の正本は keirin 側 `src/p3_calibration.confidence_pct`。
            "confidence_pct": confidence_from_entries(
                entries, r["race_type"], r["cup_grade"]),
            # 信頼度が見ている2車のうち何車が3着以内に入ったか（0/1/2・確定後のみ）。
            # 表示は 2→○ / 1→△ / 0→×。**1軸だけの的中も情報**なので潰さない。
            # 🔴 買い目の的中とは別物。相手が外れても二軸はそろっていることがある。
            "confidence_hit_count": confidence_hit_count_from_entries(entries),
            "start_at": r["start_at"],
            "n_entries": r["n_entries"],
            "axis1": r["axis1"],
            "axis2": r["axis2"],
            "title": r["title"],
            "comment": r["comment"],
            "bet_detail": detail,
            # 期待値は表示のみ。購入判断には使わないこと（上記 docstring 参照）
            "expected_value": _expected_value(lines, top3),
            # 勝負アイコン「自信あり」に選ばれた1レース（1日1件）。
            # 選定は keirin の `pick_confident_race_wt.py`。
            "is_confident": bool(r["is_confident"]),
            # 「自信あり」の選定に使った期待値（全点を予測オッズで統一して計算）。
            # 🔴 上の `expected_value` とは**別物**。あちらは板のオッズ由来で、
            #    夜開催は朝の時点で板が育っていないため終日の比較には使えない。
            "confident_ev": (float(r["confident_ev"])
                             if r["confident_ev"] is not None else None),
            # 最低払戻・最高払戻・期待値に**予測オッズが混ざっているか**。
            # 🔴 混ざっているのに黙って出すと「実際の板でこの払戻」と読まれる。
            "odds_has_predicted": any(x.get("odds_source") == "predicted" for x in lines),
            # レース信頼度指標（落車リスク）。**表示専用**（ゲートではない）。
            "crash_risk": (round(risk_by_race[r["race_key"]], 5)
                           if r["race_key"] in risk_by_race else None),
            "crash_risk_band": risk_band(risk_by_race.get(r["race_key"])),
            "mean_payout": mean_pay,
            # 想定払戻の平均が安い＝リスクに見合わない。レビュー画面の
            # 一括取消ボタンがこの印だけを見て候補を作る（2026-08-24）。
            "cheap_mean_payout": (mean_pay is not None
                                  and mean_pay <= CHEAP_MEAN_PAYOUT),
            "min_payout": lo,
            "max_payout": hi,
            # 下振れしても割らない最低払戻（`odds_low` 由来・無ければ None）。
            "min_payout_low": lo_low,
            # ガミ＝当たっても投資を下回る。
            # 🔴 判定は **下限側（`min_payout_low`）を優先する**。板由来の `odds`
            #    で測ると当たったときに実際より高い額を約束することになる
            #    （実測 中央 確定/表示 0.860・45%が0.8倍未満。`_min_payout_low` 参照）。
            #    `odds_low` が無い記録（三連単・2026-08-16 以前の入稿）は従来どおり。
            "gami_risk": (
                bool(detail and (lo_low if lo_low is not None else lo) < detail["total"])
                if detail and (lo_low is not None or lo is not None) else None),
            # ガミ判定に下限側を使えたか。使えていないなら表示側で楽観的だと分かる。
            "gami_risk_is_conservative": lo_low is not None,
            "netkeirin_race_id": r["netkeirin_race_id"],
            "proposed_at": r["proposed_at"].isoformat() if r["proposed_at"] else None,
            "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
            "deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None,
            # なぜ取り消したか（2026-08-25）。画面の「取消」バッジに添える。
            # 2026-08-25 より前の取消は記録が無いので null。
            "cancel_reason": r["cancel_reason"] or None,
            "entries": [
                {"frame_no": e["frame_no"], "name": e["name"],
                 "race_point": float(e["race_point"]) if e["race_point"] is not None else None,
                 "style": e["style"], "line_group": e["line_group"],
                 "line_pos": e["line_pos"], "player_class": e["player_class"],
                 "prediction_mark": e["prediction_mark"],
                 "pred_win_pct": float(e["pred_win_pct"]) if e["pred_win_pct"] is not None else None,
                 # 2着内率。列追加（2026-08-12）以降に算出したレースだけ値が入る
                 "pred_top2_pct": float(e["pred_top2_pct"]) if e["pred_top2_pct"] is not None else None,
                 "pred_top3_pct": float(e["pred_top3_pct"]) if e["pred_top3_pct"] is not None else None,
                 # 確定着順。**発走前は None、欠車・失格は 0**（着外）。
                 # 0 と None を潰すと「まだ走っていない」と「走ったが着外」が
                 # 区別できなくなる。
                 "finish_order": e["finish_order"]}
                for e in entries
            ],
        })
    n_proposed = sum(1 for x in items if x["status"] == STATUS_PROPOSED)
    # 未公開＝netkeirin へ送ったが公開していない（2026-08-16）。
    # ⚠️ **これは自前の記録**。netkeirin の画面から人が直接公開すると
    #    こちらは `submitted` のまま取り残されるので、実数と食い違いうる。
    #    netkeirin 側の実数は `/keirin/publish-wait` で別に取れる。
    n_unpublished = sum(1 for x in items if x["status"] == STATUS_SUBMITTED)

    # ── 確定成績（2026-08-16・ユーザー要望）──────────────────────────
    # 🔴 **`picks_history` からは出せない。** ランクのゲートを通っていない入稿
    #    （手動・看板の穴埋め）は行が立たないので、売った商品の半分が欠ける
    #    （実測 入稿472件中250件）。入稿の原本（`bet_detail`）から採点する
    #    `_fetch_settled_submissions` を使う（`/sold-performance` と同じ経路）。
    day = Date.fromisoformat(target)
    settled, _ = await _fetch_settled_submissions(
        db, day, day, None, only_missing_from_picks=False)
    by_key = {(x["race_key"], x["rank_key"]): x for x in settled}
    # 🔴 **取消分も同じ経路で採点する**（2026-08-24）。カードの「参考 買っていれば」は
    #    `bet_detail` の**入稿時点オッズ**で画面が計算していたため、確定オッズで
    #    採点するサマリーと数字が合わなかった（実測 立川4R: カード 16,910円 ↔
    #    サマリー 20,710円。同じレース・同じ的中目なのに基準違いで別の額）。
    #    突き合わせられないので、**参考値もサマリーと同じ確定オッズ**へ揃える。
    cancelled_settled, _ = await _fetch_settled_submissions(
        db, day, day, None, only_missing_from_picks=False, deleted_only=True)
    by_key_cancelled = {(x["race_key"], x["rank_key"]): x for x in cancelled_settled}
    for it in items:
        # ⚠️ 変数名に `r` を使わない。上の `for r in rows` が RowMapping で
        #    束縛済みなので、同じ名前だと mypy が代入不能として落ちる。
        got = by_key.get((it["race_key"], it["rank_key"]))
        # 未確定（発走前・確定待ち）は None。0円と区別する。
        it["result"] = None if got is None else {
            "bet": got["bet"], "payout": got["payout"],
            "hit": got["hit"],
            # 🔴 netkeirin の表示的中率は**ガミを不的中と数える**ほう。
            "net_hit": got["net_hit"],
        }
        # 🔴 **どの買い目が当たったかはサーバーで決める。** 同着では当たり目が
        #    複数になる（3着同着で三連複2通り・1着/2着同着で三連単2通り）ので、
        #    フロントで「実着順と一致するか」を組み立てると必ず取りこぼす。
        #    ここは `bet_detail.lines[].combo` とそのまま比較できる表記で返し、
        #    画面は**一致を見るだけ**にする。未確定は空配列。
        # 🔴 **売った分だけに付けてはいけない。** 取り消したレース・入稿前の
        #    レースこそ「落とした判断は正しかったか」を確認したい対象で、
        #    `got`（＝netkeirin へ送って確定した分）で絞ると赤字が出ない。
        #    着順が入っていれば付ける。
        # 🔴 **`result` へは入れない。** あちらは「売った商品の実績」で、
        #    netkeirin の成績とサマリーの回収率がそこから作られる。取消を混ぜると
        #    売っていないものが実績になる。別のキーで渡し、画面は文言で区別する。
        got_c = by_key_cancelled.get((it["race_key"], it["rank_key"]))
        it["result_if_sold"] = None if got_c is None else {
            "bet": got_c["bet"], "payout": got_c["payout"],
            "hit": got_c["hit"], "net_hit": got_c["net_hit"],
        }
        it["winning_combos"] = winning_combo_labels(
            [(e["finish_order"], e["frame_no"]) for e in it["entries"]
             if e["finish_order"] is not None])

    # ── 当日サマリー（netkeirin の表示と数字を合わせる）─────────────────
    # 🔴 母集団は **netkeirin へ送ったもの**（submitted / published）。入稿案は
    #    まだ売っていないので入れない。取消も入れない。
    # 🔴 **集計は「確定した分」だけ**。netkeirin の予想家成績も確定分しか数えない
    #    （2026-08-16 実測: netkeirin 画面が「予想数 1レース / 購入 10,000円」の
    #     とき、こちらの確定済みも 1件・10,000円で一致した）。
    #    未確定を購入に混ぜると、発走前の分だけ分母が膨らんで回収率が 0% 近くに
    #    見える＝「負けている」と誤読する。代わりに **未確定数を併記**する。
    sold = [x for x in items if x["status"] in (STATUS_SUBMITTED, STATUS_PUBLISHED)]
    settled_items = [x for x in sold if x["result"] is not None]
    bet = sum(x["result"]["bet"] for x in settled_items)
    payout = sum(x["result"]["payout"] for x in settled_items)
    n_net_hit = sum(1 for x in settled_items if x["result"]["net_hit"])
    summary = {
        # 予想数＝確定した数（netkeirin と同じ）。
        "n_races": len(settled_items),
        # まだ確定していない数。**分母には入れない**が画面には必ず出す。
        "n_pending": len(sold) - len(settled_items),
        "bet": bet,
        "payout": payout,
        "balance": payout - bet,
        "recovery_rate": (100.0 * payout / bet) if bet else None,
        # 🔴 netkeirin の表示的中率は**ガミを不的中と数える**ほう。
        "hit_rate": (100.0 * n_net_hit / len(settled_items)) if settled_items else None,
    }
    # ── 取り消した分の「そのまま売っていたら」（2026-08-24・ユーザー要望）──
    # 🔴 **実績ではない。** 売っていないので netkeirin の成績にも入らないし、
    #    上の `summary` にも入れない。落とした判断が正しかったかを見るための参考値。
    # 🔴 採点は**実績と同じ経路**（`_fetch_settled_submissions`）を使う。確定オッズ
    #    （`wt_odds`）で採点するので、`bet_detail` の入稿時点オッズで画面が計算する
    #    のとは別物。画面側で計算すると実績サマリーと数字の作り方が食い違う。
    # ⚠️ 母集団は**取り消したレースだけ**。入稿前（proposed）は「まだ売っていない」
    #    のであって「売らないと決めた」ではないので混ぜない。
    c_bet = sum(x["bet"] for x in cancelled_settled)
    c_pay = sum(x["payout"] for x in cancelled_settled)
    c_net = sum(1 for x in cancelled_settled if x["net_hit"])
    # 🔴 **未確定数は実数を返す**（2026-08-24 是正）。当初「取消は売っていないので
    #    未確定の概念を持たない」として 0 固定にしていたが、画面が**常時表示**へ
    #    変わったことで「取消14件のうち確定は2件」という状態が
    #    「予想数 2レース」としか出ず、**残り12件が消えたように見える**。
    #    確定していないだけで、走れば数字が入る。
    n_cancelled = sum(1 for x in items if x["status"] == STATUS_DELETED)
    summary_cancelled = {
        "n_races": len(cancelled_settled),
        "n_pending": max(0, n_cancelled - len(cancelled_settled)),
        "bet": c_bet,
        "payout": c_pay,
        "balance": c_pay - c_bet,
        "recovery_rate": (100.0 * c_pay / c_bet) if c_bet else None,
        "hit_rate": (100.0 * c_net / len(cancelled_settled)) if cancelled_settled else None,
    }
    return JSONResponse(content={"date": target, "n_proposed": n_proposed,
                                 "n_unpublished": n_unpublished,
                                 "summary": summary,
                                 "summary_cancelled": summary_cancelled,
                                 "items": items})


class ApprovalIn(BaseModel):
    """レース単位（race_key + rank_key）か場単位（date + venue_name）。"""

    race_key: str | None = None
    rank_key: str | None = None
    date: str | None = None
    venue_name: str | None = None
    # 取消専用。netkeirin 側の削除をあきらめて記録だけ取消にする。
    # 既定 False。承認では無視する（keirin 側の CLI も cancel 以外では弾く）。
    force: bool = False
    # date で指定した日の**全場・全件**を対象にする（取消 2026-08-12 / 承認 2026-08-16）。
    # 🔴 date が無ければ受け付けない（過去分まで巻き込むため）。
    all_venues: bool = False
    # 承認専用。入稿が通ったものを続けて **netkeirin で公開**する（2026-08-16）。
    # 🔴 **公開は不可逆**（netkeirin の確認文言「公開後は修正できなくなります」）。
    #    画面側で必ず人の確認を挟むこと。keirin 側 CLI も approve 以外では弾く。
    publish: bool = False
    # 取消専用。**なぜ取り消したか**（2026-08-25）。一覧の「取消」バッジに出る。
    # 🔴 画面のボタンごとに固定の文言を送る（自由入力ではない）。理由が無いと
    #    「売っていない」ことは分かっても「なぜ」が画面から永久に消える。
    reason: str | None = None


async def _closed_races(race_keys: Sequence[str]) -> dict[str, bool]:
    """race_key → 入稿の締切（発走15分前）を過ぎているか。

    🔴 判定は `keirin_submission_window`（正本）に委ねる。ここで秒数を書かない。
    """
    if not race_keys:
        return {}
    now = datetime.now(UTC).timestamp()
    out: dict[str, bool] = {}
    async for db in get_db():
        rows = (await db.execute(
            text("SELECT race_key, start_at FROM keirin.wt_races "
                 "WHERE race_key = ANY(:keys)"),
            {"keys": list(race_keys)},
        )).fetchall()
        for r in rows:
            out[r.race_key] = is_closed(r.start_at, now)
        break
    return out


def _closed_response(labels: Sequence[str]) -> JSONResponse:
    """締切超過で拒むときの応答。**理由と対象を必ず返す**（画面に出すため）。"""
    n = SUBMIT_DEADLINE_SEC // 60
    return JSONResponse(
        content={"ok": False,
                 "message": (f"発走{n}分前を過ぎているため操作できません: "
                             f"{', '.join(labels)}")},
        status_code=409)


async def _call_webhook(path: str, payload: dict) -> JSONResponse:
    try:
        async with httpx.AsyncClient() as client:
            # 承認は netkeirin への POST を伴い同期で走る。webhook 側は
            # timeout=180 なので、こちらはそれより長く待つ。
            r = await client.post(f"{_WEBHOOK_BASE}{path}", json=payload, timeout=200.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "message": str(exc)}, status_code=503)


@router.post("/approve")
async def approve_proposal(body: ApprovalIn, _: ApiKeyDep) -> JSONResponse:
    """入稿案を承認して netkeirin へ送る（レース単位 / 場単位）。

    🔴 keirin 側は**保存済みの買い目をそのまま**送る。ここで買い目を組み直したり
       渡したりしない（確認画面で見たものと違うものが入稿される）。
    """
    if body.race_key and body.rank_key:
        base = body.race_key.split("#", 1)[0]
        if not _RACE_KEY_RE.match(base):
            return JSONResponse(content={"ok": False, "message": f"不正なrace_key: {body.race_key}"},
                                status_code=400)
        # 🔴 締切（発走15分前）を過ぎたら netkeirin が受け付けない。
        #    押せてしまうと「押したのに出ていない」に見えるので手前で拒む。
        if (await _closed_races([base])).get(base):
            return _closed_response([base])
        return await _call_webhook(
            "/approve", {"race_key": base, "rank_key": body.rank_key,
                         "publish": body.publish})
    if body.date and (body.venue_name or body.all_venues):
        if not _DATE_RE.match(body.date):
            return JSONResponse(content={"ok": False, "message": f"不正な日付: {body.date}"},
                                status_code=400)
        # 🔴 その日の全場をまとめて承認する（2026-08-16・ユーザー要望）。
        #    取消には元から全件があり、承認だけ場単位止まりで非対称だった。
        #    事故防止は取消と同じ作法に揃える —— **date 必須**で範囲を縛り
        #    （日付の無い全件承認は通さない）、画面側で二段確認と件数表示を出す。
        #    ⚠️ 対象は `proposed` のみ（CLI 側 `_proposals_for_venue`）。
        #       既に送った `submitted` を混ぜると二重入稿になる。
        payload: dict[str, Any] = {"date": body.date}
        if body.venue_name:
            payload["venue_name"] = body.venue_name
        if body.all_venues:
            payload["all_venues"] = True
        if body.publish:
            payload["publish"] = True
        return await _call_webhook("/approve", payload)
    return JSONResponse(
        content={"ok": False,
                 "message": "race_key+rank_key か date+venue_name か date+all_venues が必要です"},
        status_code=400)


@router.post("/publish")
async def publish_submission(body: ApprovalIn, _: ApiKeyDep) -> JSONResponse:
    """公開待ち（netkeirin へ送信済み）の入稿を**公開する**（2026-08-16）。

    netkeirin では「入稿（下書きとして送る）」と「公開」が別操作で、公開は
    `race_auth.html` で人が押していた。確認画面から押せるようにする。

    🔴 **公開は不可逆**。netkeirin 自身の確認文言が「公開後は修正できなくなります」。
       画面側で必ず人の確認を挟むこと。
    🔴 対象は `submitted` のみ。入稿案（proposed）は netkeirin にまだ無いので
       公開できない（「入稿してから公開」は `/approve` に `publish: true`）。
    ⚠️ 締切（発走15分前）を過ぎたレースは netkeirin の画面でも押させない
       （JS の `check_closetime`）。keirin 側 CLI が同じ関門で落として明細に載せる。
    """
    if body.race_key and body.rank_key:
        base = body.race_key.split("#", 1)[0]
        if not _RACE_KEY_RE.match(base):
            return JSONResponse(content={"ok": False, "message": f"不正なrace_key: {body.race_key}"},
                                status_code=400)
        if (await _closed_races([base])).get(base):
            return _closed_response([base])
        return await _call_webhook("/publish", {"race_key": base, "rank_key": body.rank_key})
    if body.date and (body.venue_name or body.all_venues):
        if not _DATE_RE.match(body.date):
            return JSONResponse(content={"ok": False, "message": f"不正な日付: {body.date}"},
                                status_code=400)
        payload: dict[str, Any] = {"date": body.date}
        if body.venue_name:
            payload["venue_name"] = body.venue_name
        if body.all_venues:
            payload["all_venues"] = True
        return await _call_webhook("/publish", payload)
    return JSONResponse(
        content={"ok": False,
                 "message": "race_key+rank_key か date+venue_name か date+all_venues が必要です"},
        status_code=400)


@router.get("/publish-wait")
async def publish_wait(_: ApiKeyDep) -> JSONResponse:
    """netkeirin 側の**公開待ち件数**（読み取り専用）。

    こちらの `netkeirin_submissions.status` は、netkeirin の画面から人が直接
    公開すると `submitted` のまま取り残される。2つの数字を並べて食い違いを
    見えるようにするための口（食い違い自体が「画面外で操作された」情報）。
    """
    # 🔴 **POST で呼ぶ。** webhook 側は `do_POST` にしか口が無く（`do_GET` は
    #    /health だけ）、GET だと 404 が返って「取得できませんでした」に化ける。
    #    読み取り専用でも HTTP メソッドは webhook 側に合わせること。
    #    検査: tests/test_keirin_proposals_api.py::test_publish_wait_is_posted_to_webhook
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_WEBHOOK_BASE}/publish-wait", json={}, timeout=70.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        # 🔴 付随情報なので画面を落とさない。ただし ok=False は必ず立てる
        #    （0件と「取れなかった」を画面側が区別できるように）。
        return JSONResponse(content={"ok": False, "count": 0, "message": str(exc)},
                            status_code=200)


@router.post("/publish-sync")
async def publish_sync(body: ApprovalIn, _: ApiKeyDep) -> JSONResponse:
    """netkeirin で公開された分を、こちらの記録へ反映する（2026-08-19）。

    netkeirin の公開待ち一覧に載っていない `submitted` を `published` にする。

    🔴 **date 必須**（日付の無い全件更新は通さない。取消・承認と同じ作法）。
    🔴 逆向き（published → submitted）はしない。公開は不可逆なので必ず誤りになる。
    ⚠️ 「公開された」と「netkeirin 側で削除された」は区別できない
       （netkeirin に公開済み一覧の API が無い）。画面の確認文言でそう説明すること。
    """
    if not body.date or not _DATE_RE.match(body.date):
        return JSONResponse(content={"ok": False, "message": f"不正な日付: {body.date}"},
                            status_code=400)
    return await _call_webhook("/publish-sync", {"date": body.date})


@router.post("/cancel")
async def cancel_proposal(body: ApprovalIn, _: ApiKeyDep) -> JSONResponse:
    """入稿を取り消す（netkeirin の下書きを削除・記録は論理削除）。

    レース単位（race_key + rank_key）／場単位（date + venue_name）／
    その日の全件（date + all_venues）を受け付ける。

    🔴 **場単位・全件は 2026-08-12 に追加した**（元は「まとめて消す事故を避ける」
       ため承認のみだった）。事故防止は**画面の二段確認と件数表示**に移してある。
       API 側は **date 必須**で範囲を縛る —— 日付の無い全件取消は絶対に通さない。
    ⚠️ 対象は生きている下書き（proposed / submitted）。取消済みは含めない。
    ⚠️ netkeirin 側の削除が効くのは**公開待ち**のもの。公開済みに効くかは未確認。
    ⚠️ **一括では force を使わない。** 失敗した分は明細で返すので、
       netkeirin 側に無いものは画面から1件ずつ強制取消すること。

    `force=true` は **netkeirin を触らず記録だけ取消にする**。netkeirin 側で先に
    下書きを消していると item_id が引けず、従来はそこで止まって **DB も更新されない**
    ままだった（取消したはずの行が残り、自動穴埋めの重複判定にも引っかかる）。
    """
    if body.race_key and body.rank_key:
        base = body.race_key.split("#", 1)[0]
        if not _RACE_KEY_RE.match(base):
            return JSONResponse(
                content={"ok": False, "message": f"不正なrace_key: {body.race_key}"},
                status_code=400)
        # 🔴 締切後は netkeirin 側の下書き削除も効かない。ただし `force` は
        #    **netkeirin を触らず記録だけ取消にする**ので締切に関係なく通す
        #    （締切を過ぎた行を永久に片付けられなくなるのを避ける逃げ道）。
        if not body.force and (await _closed_races([base])).get(base):
            return _closed_response([base])
        payload_one: dict[str, Any] = {
            "race_key": base, "rank_key": body.rank_key, "force": body.force}
        if body.reason:
            payload_one["reason"] = body.reason[:255]
        return await _call_webhook("/cancel", payload_one)

    if body.date and (body.venue_name or body.all_venues):
        if not _DATE_RE.match(body.date):
            return JSONResponse(content={"ok": False, "message": f"不正な日付: {body.date}"},
                                status_code=400)
        payload: dict[str, Any] = {"date": body.date}
        if body.venue_name:
            payload["venue_name"] = body.venue_name
        if body.all_venues:
            payload["all_venues"] = True
        if body.reason:
            payload["reason"] = body.reason[:255]
        return await _call_webhook("/cancel", payload)

    return JSONResponse(
        content={"ok": False,
                 "message": "race_key+rank_key か date+venue_name か date+all_venues が必要です"},
        status_code=400)


class ApprovalModeIn(BaseModel):
    require_approval: bool


@router.get("/approval-mode")
async def get_approval_mode(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    row = (await db.execute(text(
        "SELECT require_approval FROM keirin.netkeirin_settings WHERE rank_key = '_global'"
    ))).mappings().first()
    return JSONResponse(content={"require_approval": bool(row["require_approval"]) if row else False})


@router.put("/approval-mode")
async def set_approval_mode(body: ApprovalModeIn, _: ApiKeyDep,
                            db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """承認制の ON/OFF。

    承認制は一時運用の想定なので、**画面から戻せる**ようにしてある
    （コード変更もデプロイも要らない）。
    ⚠️ ON にすると承認するまで netkeirin へ何も出ない。
    """
    await db.execute(text(
        "UPDATE keirin.netkeirin_settings SET require_approval = :v, updated_at = NOW() "
        "WHERE rank_key = '_global'"
    ), {"v": body.require_approval})
    await db.commit()
    return JSONResponse(content={"ok": True, "require_approval": body.require_approval})
