"""競輪 picks/summary API ルーター

keirin スキーマ（PostgreSQL）を参照して結果を返す。

GET /api/keirin/picks?date=YYYY-MM-DD   - 指定日の推奨ピック一覧
GET /api/keirin/summary                  - 当日/当月/当年の投資・回収サマリー
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
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
from ..db.session import get_db
from ..services.keirin_marquee import is_marquee_race
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
_PAPER_RANK_LABELS: dict[str, str] = {
    "RANK_7SS": "7SS",
    "RANK_7S": "7S",
    "RANK_7A": "7A",
    # RANK_7B: 2026-08-03 新設。軸2車がWT公式印◎◯と完全一致するが、順序
    # （モデル1位≠◎）と相手（△を買い目から除外）で市場と不一致なレース。
    # 7S/7A が枯渇（overlap∈{0,1}が18〜23%まで低下）したことへの増枠。
    # 三連複の相手絞り3点で 7S/7A の5点総流しとは点数が異なる。
    "RANK_7B": "7B",
    "RANK_9S": "9S",
    "RANK_9A": "9A",
    # RANK_7H1: 2026-08-06 新設の**穴推奨**（本命バスト型）。既存6ランクとは系統が
    # 違う（S/A/B＝的中率重視の予想ベース、H＝穴狙い）。命名は `{車数}H{連番}`。
    # 「当方指数で頭ひとつ抜けた1車が4着以下に沈む」とレース単位モデルが読んだ
    # 7車立てだけを選び、**その本命と同ラインを買い目から丸ごと落とす**。
    # ⚠️ **唯一の2券種ランク**（三連単フォーメーション8点 + 三連複BOX 4〜10点）。
    #    picks_history には 1レース1行で合算して記録され（race_key サフィックス
    #    `#7H1`・pred_combo は "三複:… / 三単:…"）、券種別の払戻は
    #    trio_payout / trifecta_payout に入る。買い目文字列の形が他ランクと
    #    異なるため、合成オッズ（_parse_combinations）は算出対象外になる
    #    （パース失敗で None を返すので表示が壊れることはない）。
    # 手動入稿（_MANUAL_RANK_KEYS）は軸2車を選ぶUIのため**対象外**。
    "RANK_7H1": "7H1",
    # RANK_7C: 2026-08-07 新設の**ベースモデル**（終日の二軸）。既存6ランクと
    # 違い wt_overlap_n を見ないため**同一レースに併存しうる**（picks_history の
    # race_key は `{レースキー}#{suffix}` なので行は共存できる）。
    "RANK_7C": "7C",
    # RANK_9H1: 2026-08-08 新設の**穴推奨**（9車・高配当狙い）。7H1 と同じ穴推奨系だが
    #    **車数で母集団が完全に排他**（7H1=7車ちょうど / 9H1=9車ちょうど）。
    #    券種は三連単フォーメーションの**単一券種**（6点）で、pred_combo は "三単:…"。
    #    2券種の 7H1 と違い払戻は payout だけに入る（trio_payout は常に0）。
    # 手動入稿（_MANUAL_RANK_KEYS）は軸2車を選ぶUIのため**対象外**（7H1 と同じ理由）。
    "RANK_9H1": "9H1",
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


def _finish_top3_frames(entries: Sequence[Any]) -> list[int] | None:
    """確定した1〜3着の車番を着順で返す。未確定・欠けているなら None。"""
    top3 = sorted(
        (e for e in entries
         if e["finish_order"] is not None and 1 <= e["finish_order"] <= 3),
        key=lambda e: e["finish_order"],
    )
    if len(top3) != 3:
        return None
    return [int(e["frame_no"]) for e in top3]


async def _manual_submission_buckets(
    db: AsyncSession, from_dt: Date, to_dt: Date,
    rank_labels: list[str] | None, monthly: bool,
) -> tuple[dict[str, dict[str, int]], int]:
    """ゲート未通過の入稿（手動・看板の穴埋め）を成績集計用に日付バケットへ畳む。

    `picks_history` に行が無いので採点もされていない。**`/picks` と同じ
    `_submitted_pick_result()` を使って**入稿の原本と確定結果から組み立てる
    ―― 集計用に別の計算を書くと、一覧の数字と成績の数字が静かに食い違う。

    rank_labels が None なら全ランク。空リストは「該当なし」ではなく全ランク扱い
    （`/stats` の rank パラメータが未知値で全体へフォールバックする仕様と揃える）。

    戻り値は (バケット, 買い目が記録されていなかった件数)。
    🔴 **`bet_detail` の保存は 2026-08-07 開始**。それ以前の手動入稿は
       「入稿した事実」しか残っておらず、買い目も金額も復元できない。
       0円として足すと投資額を過小に見せるので**集計から外し、件数だけ返す**
       （黙って落とすと「全入稿」が完全な数字に見えてしまう）。
    """
    rank_cond = ""
    params: dict[str, Any] = {"from_date": from_dt.isoformat(), "to_date": to_dt.isoformat()}
    if rank_labels:
        rank_cond = "AND ns.rank_key = ANY(:rank_keys)"
        params["rank_keys"] = rank_labels

    subs = (await db.execute(
        text(f"""
            SELECT ns.race_key, ns.rank_key, ns.bet_detail, wr.race_date
            FROM keirin.netkeirin_submissions ns
            JOIN keirin.wt_races wr ON wr.race_key = ns.race_key
            WHERE wr.race_date BETWEEN :from_date AND :to_date
              AND ns.deleted_at IS NULL
              {rank_cond}
              -- 発走から90分。picks_history 側の集計条件と同一にする
              AND (
                wr.status = 3
                OR (wr.start_at IS NOT NULL
                    AND wr.start_at::BIGINT + 5400 < EXTRACT(EPOCH FROM NOW()))
              )
              AND NOT EXISTS (
                SELECT 1 FROM keirin.picks_history ph2
                WHERE SPLIT_PART(ph2.race_key, '#', 1) = ns.race_key
                  AND ph2.route = 'wt'
                  AND ph2.rank IN {_VALID_PICK_RANKS}
                  AND {_enabled_rank_cond('ph2')}
              )
        """),
        params,
    )).mappings().all()
    if not subs:
        return {}, 0

    keys = sorted({s["race_key"] for s in subs})
    # 確定着順（1〜3着）をまとめて引く
    finishes: dict[str, list] = {}
    for e in (await db.execute(
        text("""
            SELECT race_key, frame_no, finish_order FROM keirin.wt_entries
            WHERE race_key = ANY(:keys) AND finish_order BETWEEN 1 AND 3
        """), {"keys": keys},
    )).mappings().all():
        finishes.setdefault(e["race_key"], []).append(dict(e))

    # 当たり目のオッズをまとめて引く（レースごとに1〜2行）
    odds_by_race: dict[str, dict[str, int]] = {}
    wanted: list[dict[str, str]] = []
    win_frames: dict[str, list[int]] = {}
    for rk in keys:
        frames = _finish_top3_frames(finishes.get(rk, []))
        if not frames:
            continue
        win_frames[rk] = frames
        wanted.append({"rk": rk, "tc": "-".join(map(str, sorted(frames))),
                       "fc": "-".join(map(str, frames))})
    if wanted:
        for o in (await db.execute(
            text("""
                SELECT race_key, bet_type, odds_value FROM keirin.wt_odds
                WHERE (race_key, bet_type, combination) IN (
                    SELECT w.rk, t.bt, CASE t.bt WHEN 'trio' THEN w.tc ELSE w.fc END
                    FROM jsonb_to_recordset(CAST(:w AS jsonb))
                         AS w(rk text, tc text, fc text),
                         (VALUES ('trio'), ('trifecta')) AS t(bt)
                )
            """), {"w": json.dumps(wanted)},
        )).mappings().all():
            if not o["odds_value"]:
                continue
            pay = int(round(float(o["odds_value"]) * 100)) // 10 * 10
            odds_by_race.setdefault(o["race_key"], {})[o["bet_type"]] = pay

    buckets: dict[str, dict[str, int]] = {}
    n_missing = 0
    for s in subs:
        rk = s["race_key"]
        pays = odds_by_race.get(rk, {})
        res = _submitted_pick_result(
            _parse_bet_detail(s["bet_detail"]), win_frames.get(rk),
            pays.get("trio", 0), pays.get("trifecta", 0),
        )
        if res["bet_amount"] <= 0:
            # 買い目が記録されていない（2026-08-07 以前）。件数だけ数えて集計から外す。
            n_missing += 1
            continue
        date_str = str(s["race_date"])
        key = date_str[:7] if monthly else date_str
        b = buckets.setdefault(key, {"n_picks": 0, "n_hits": 0, "total_bet": 0, "total_payout": 0})
        b["n_picks"] += 1
        b["n_hits"] += 1 if res["hit"] else 0
        b["total_bet"] += res["bet_amount"]
        b["total_payout"] += res["payout"]
    return buckets, n_missing


def _submitted_pick_result(
    bet: dict[str, Any] | None, finish_frames: list[int] | None,
    trio_pay: int, trifecta_pay: int,
) -> dict[str, Any]:
    """入稿記録だけの行（picks_history に無い）の買い目・投資・的中・払戻を組む。

    ランクのゲートを通っていないレースは `picks_history` に行が立たず、採点バッチも
    走らない。そのため **入稿の原本（`bet_detail`）と確定結果から直に組み立てる**。

    ⚠️ **買い目は再構成しない。** 入稿の瞬間に保存した combo と stake をそのまま使う。
       傾斜配分は入稿時点の想定オッズで決まるので後から再現できない。
    ⚠️ combo の区切りは券種で違う（三連複 `1=2=4` / 三連単 `1-2-4`）。
       ここを取り違えると**当たっているのに不的中**になる（表示だけ静かに壊れる）。
    """
    lines = (bet or {}).get("lines") or []
    combos = [str(x["combo"]) for x in lines]
    out: dict[str, Any] = {
        "pred_combo": " ".join(combos) or None,
        "n_combos": len(lines) or None,
        "bet_amount": sum(int(x["stake"]) for x in lines),
        "hit": False,
        "payout": 0,
    }
    if not lines or not finish_frames:
        return out

    win_trio = "=".join(map(str, sorted(finish_frames)))
    win_trifecta = "-".join(map(str, finish_frames))
    payout = 0
    for x in lines:
        combo = str(x["combo"])
        stake = int(x["stake"])
        # 100円あたりの払戻 × 賭け金/100。10円未満は切り捨てない
        # （trio_pay 自体が既に10円単位で丸めてある）。
        if combo == win_trio:
            payout += trio_pay * stake // 100
        elif combo == win_trifecta:
            payout += trifecta_pay * stake // 100
    out["hit"] = payout > 0
    out["payout"] = payout
    return out

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
                  ph.gate_label
                FROM keirin.wt_races wr
                JOIN keirin.venue_info vi
                  ON wr.venue_id = vi.venue_code
                LEFT JOIN keirin.picks_history ph
                  ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
                 AND ph.race_date = :date
                 AND ph.route = 'wt'
                 AND ph.rank IN {_VALID_PICK_RANKS}
                  AND {_enabled_rank_cond('ph')}
                -- 生きている入稿のうち最新の1件（取消は論理削除なので除外する）
                LEFT JOIN LATERAL (
                    SELECT rank_key
                    FROM keirin.netkeirin_submissions x
                    WHERE x.race_key = wr.race_key AND x.deleted_at IS NULL
                    ORDER BY x.submitted_at DESC
                    LIMIT 1
                ) ns ON TRUE
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
                  vi.name AS venue_name
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
    submitted = {
        (m["race_key"], m["rank_key"]): m["bet_detail"]
        for m in (await db.execute(
            text("""
                SELECT ns.race_key, ns.rank_key, ns.bet_detail
                FROM keirin.netkeirin_submissions ns
                JOIN keirin.wt_races wr ON wr.race_key = ns.race_key
                WHERE wr.race_date = :date
            """),
            {"date": target},
        )).mappings().all()
    }

    picks = []
    for r in rows:
        base_key = r["base_key"]
        has_pick = r["rank"] is not None
        # 入稿記録だけの行（ゲート未通過で picks_history に無い）。
        submission_only = bool(r.get("submission_only"))

        if has_pick:
            is_wide = r["rank"] == "WIDE"
            race_key = r["ph_race_key"] if include_all else r["race_key"]
            # 合成オッズは picks_history の pred_combo が前提。入稿だけの行には無い。
            synth_odds = (None if submission_only
                          else await _calc_synth_odds(db, base_key, r["pred_combo"], is_wide))
        else:
            race_key = base_key
            synth_odds = None

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

        # 確定した上位3着（レース未確定なら None）。払戻の算出と、入稿だけの行の
        # 的中判定の両方で使う。
        finish_frames = _finish_top3_frames(entries)

        # 推奨外レース・採点前の候補行でも、レース確定後は三連複/三連単の払戻を表示する。
        # picks_history に未記録（0円）の場合は wt_odds の最終オッズ×100 から算出
        # （10円単位切り捨て。実払戻との一致は 2026-07-12 に検証済み）。
        trio_pay = int(r["trio_payout"] or 0) if has_pick else 0
        trifecta_pay = int(r["trifecta_payout"] or 0) if has_pick else 0
        if trio_pay == 0 or trifecta_pay == 0:
            if finish_frames:
                frames = finish_frames
                trio_comb = "-".join(map(str, sorted(frames)))
                tri_comb = "-".join(map(str, frames))
                odds_rows = (await db.execute(
                    text("""
                        SELECT bet_type, odds_value
                        FROM keirin.wt_odds
                        WHERE race_key = :bk
                          AND ((bet_type = 'trio' AND combination = :tc)
                            OR (bet_type = 'trifecta' AND combination = :fc))
                    """),
                    {"bk": base_key, "tc": trio_comb, "fc": tri_comb},
                )).mappings().all()
                for o in odds_rows:
                    if not o["odds_value"]:
                        continue
                    pay = int(round(float(o["odds_value"]) * 100)) // 10 * 10
                    if o["bet_type"] == "trio" and trio_pay == 0:
                        trio_pay = pay
                    elif o["bet_type"] == "trifecta" and trifecta_pay == 0:
                        trifecta_pay = pay

        # 入稿の原本（keirin 側が入稿の瞬間に保存した買い目と金額配分）。
        submitted_bet = _parse_bet_detail(
            submitted.get((base_key, (r["rank"] or "").replace("RANK_", ""))))
        # 入稿だけの行は、買い目・投資・的中・払戻をその原本と確定結果から組む。
        sub_result = (_submitted_pick_result(submitted_bet, finish_frames, trio_pay, trifecta_pay)
                      if submission_only else None)

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
            "pred_combo": (sub_result["pred_combo"] if sub_result
                           else (r["pred_combo"] if has_pick else None)),
            "n_combos": (sub_result["n_combos"] if sub_result
                         else (r["n_combos"] if has_pick else None)),
            "synth_odds": synth_odds,
            "hit": (sub_result["hit"] if sub_result
                    else (bool(r["hit"]) if has_pick else False)),
            "payout": (sub_result["payout"] if sub_result
                       else ((r["payout"] or 0) if has_pick else 0)),
            "trio_payout": trio_pay,
            "trifecta_payout": trifecta_pay,
            "bet_amount": (sub_result["bet_amount"] if sub_result
                           else ((r["bet_amount"] or 0) if has_pick else 0)),
            # ゲートを通っていない入稿（手動・看板の穴埋め）であることを表に出す。
            # 混ぜたまま出すと「ランクの成績」と読まれてしまう。
            "submission_only": submission_only,
            # 入稿の出自。`submission_only` だけだと**看板の穴埋め（自動）も
            # 手動入稿も同じに見える**ので、バッジの出し分けにはこちらを使う。
            "origin": r.get("origin") or None,
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

    未知の rank（全廃済みランクの残骸データ等）は元の rank 文字列をそのまま
    返す（呼び出し側は _VALID_PICK_RANKS/_RANKS_ALL のallowlistで事前に
    除外している想定のため、通常この分岐には到達しない）。
    """
    return _PAPER_RANK_LABELS.get(rank, rank)


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
) -> dict:
    # 2026-08-01〜: 現行ランクは _PAPER_RANK_LABELS の5ランク（RANK_7S/RANK_7A/
    # RANK_9S/RANK_9A）。gate_labelによる表示分岐は廃止済み（_display_rank
    # 参照）。旧S1(SEVEN_S1)・旧S2=7PLUS_U・旧S3=7PLUS_M は全廃・行はアーカイブ
    # 退避 or 残骸のまま（allowlist方式のため自動的に集計対象から除外される）。
    # rank_filter: 個別ランクだけの集計にも本関数を再利用できるようパラメータ化
    # （既定は現行有効ランク全て）。
    row = (await db.execute(
        text(f"""
            SELECT
              COUNT(*)                                                          AS n_picks,
              SUM(ph.hit)                                                       AS n_hits,
              COALESCE(SUM(ph.bet_amount), 0)                                   AS total_bet,
              COALESCE(SUM(CASE WHEN ph.hit = 1 THEN ph.payout ELSE 0 END), 0) AS total_payout,
              MAX(CASE WHEN ph.hit = 1 THEN ph.payout ELSE NULL END)            AS max_payout
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE {where}
              AND NOT COALESCE(ph.miwokuri, FALSE)
              AND ph.bet_amount > 0
              AND ph.rank IN {rank_filter}
              AND {_enabled_rank_cond()}
              AND ph.race_key NOT LIKE '%#CAND'
              AND {_SETTLED_COND}
        """),
        params,
    )).mappings().one_or_none()

    if not row:
        return {"n_picks": 0, "n_hits": 0, "total_bet": 0, "total_payout": 0, "roi": None,
                "max_payout": None, "n_candidates": 0, "by_rank": {}}

    n_picks = int(row["n_picks"] or 0)
    n_hits = int(row["n_hits"] or 0)
    total_bet = int(row["total_bet"] or 0)
    total_payout = int(row["total_payout"] or 0)
    max_payout = int(row["max_payout"]) if row["max_payout"] is not None else None
    result = _make_period_dict(n_picks, n_hits, total_bet, total_payout, max_payout)

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
    rank_rows = (await db.execute(
        text(f"""
            SELECT
              ph.rank                                                            AS rank,
              COUNT(*)                                                           AS n_picks,
              SUM(ph.hit)                                                        AS n_hits,
              COALESCE(SUM(ph.bet_amount), 0)                                    AS total_bet,
              COALESCE(SUM(CASE WHEN ph.hit = 1 THEN ph.payout ELSE 0 END), 0)  AS total_payout,
              MAX(CASE WHEN ph.hit = 1 THEN ph.payout ELSE NULL END)             AS max_payout
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE {where}
              AND NOT COALESCE(ph.miwokuri, FALSE)
              AND ph.bet_amount > 0
              AND ph.rank IN {rank_filter}
              AND {_enabled_rank_cond()}
              AND ph.race_key NOT LIKE '%#CAND'
              AND {_SETTLED_COND}
            GROUP BY ph.rank
        """),
        params,
    )).mappings().all()

    by_rank: dict[str, dict] = {}
    for r in rank_rows:
        key = _display_rank(str(r["rank"]))
        by_rank[key] = _make_period_dict(
            int(r["n_picks"] or 0),
            int(r["n_hits"] or 0),
            int(r["total_bet"] or 0),
            int(r["total_payout"] or 0),
            int(r["max_payout"]) if r["max_payout"] is not None else None,
        )

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
_MANUAL_RANK_KEYS = ("7S", "7A", "9S", "9A")


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

    include_manual: ランクのゲートを通っていない入稿（手動入稿・看板の穴埋め）を
        含めるか。既定は False＝**ゲートを通った推奨だけ**の成績。
        🔴 含めると数字の意味が変わる。False は「ランクの実力」、True は
        「実際に賭けた全額の収支」。実測（直近30日）で ROI が 0.711 → 約0.67 動く。
        入稿だけの行は採点されないため、`/picks` と同じ `_submitted_pick_result()`
        で入稿の原本と確定結果から組み立てる（別計算にすると一覧と食い違う）。

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

    if granularity == "monthly":
        date_expr = "TO_CHAR(ph.race_date::DATE, 'YYYY-MM')"
    else:
        date_expr = "ph.race_date"

    # rank クエリパラメータはホワイトリスト方式で固定SQL文字列に変換する
    # （rank文字列をそのままSQLへ埋め込まない）。カンマ区切りで複数指定された場合は
    # OR条件として結合する（例: "7A,9S" → RANK_7A or RANK_9S）。
    # 2026-08-01〜: gate_labelによる分岐は廃止済み・内部rankは_PAPER_RANK_LABELSの
    # 単純な等価条件になる。既定の"all"は全ランクをまとめて集計する（/summaryと同じ方針）。
    #
    # 【2026-08-05 是正】ここは _PAPER_RANK_LABELS とは別に手書きの辞書を持っており、
    # 2026-08-03 に新設した 7B が追加されないまま放置されていた。未知キーは
    # `_matched_conds` が空になり **黙って _ALL_COND（全ランク）へフォールバック**
    # するため、統計ページで「7B」を選ぶと全ランクの数字が 7B として表示されていた
    # （エラーも警告も出ない）。ランク名の二重管理をやめ _PAPER_RANK_LABELS から
    # 導出する（同辞書のコメントが宣言している「新ランク追加時はここだけ直せばよい」
    # 設計を実際に満たすため）。
    _RANK_COND_MAP = {
        label: f"ph.rank = '{internal}'"
        for internal, label in _PAPER_RANK_LABELS.items()
    }
    _ALL_COND = f"ph.rank IN {_RANKS_ALL}"
    _requested_keys = [k.strip() for k in rank.split(",") if k.strip()]
    if not _requested_keys or "all" in _requested_keys:
        _RANK_COND = _ALL_COND
    else:
        _matched_conds = [_RANK_COND_MAP[k] for k in _requested_keys if k in _RANK_COND_MAP]
        # 複数条件はOR結合するため、AND {_RANK_COND} の文脈で優先順位が壊れないよう
        # 常に外側を括弧で囲む（単一条件でも一貫性のため同様に囲む）
        _RANK_COND = "(" + " OR ".join(f"({c})" for c in _matched_conds) + ")" if _matched_conds else _ALL_COND

    _STATS_COND = f"""
        AND NOT COALESCE(ph.miwokuri, FALSE)
        AND ph.bet_amount > 0
        AND {_RANK_COND}
        AND ph.race_key NOT LIKE '%#CAND'
        AND (
            wr.status = 3
            OR (wr.start_at IS NOT NULL AND wr.start_at::BIGINT + 5400 < EXTRACT(EPOCH FROM NOW()))
        )
    """

    rows = (await db.execute(
        text(f"""
            SELECT
                {date_expr}                                                           AS bucket,
                COUNT(*)                                                              AS n_picks,
                COALESCE(SUM(ph.hit), 0)                                              AS n_hits,
                COALESCE(SUM(ph.bet_amount), 0)                                       AS total_bet,
                COALESCE(SUM(CASE WHEN ph.hit = 1 THEN ph.payout ELSE 0 END), 0)     AS total_payout
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE ph.race_date BETWEEN :from_date AND :to_date
            {_STATS_COND}
            GROUP BY {date_expr}
            ORDER BY {date_expr}
        """),
        {"from_date": from_dt.isoformat(), "to_date": to_dt.isoformat()},
    )).mappings().all()
    # 手動入稿分を足すため、以降は素の dict で扱う（RowMapping は不変）。
    stat_rows: list[dict[str, Any]] = [dict(r) for r in rows]

    manual_missing = 0
    if include_manual:
        manual, manual_missing = await _manual_submission_buckets(
            db, from_dt, to_dt,
            # "all"・未知値のときは None（＝全ランク）。picks_history 側の
            # フォールバック仕様と揃える。
            None if _RANK_COND is _ALL_COND else [
                k for k in _requested_keys if k in _RANK_COND_MAP],
            granularity == "monthly",
        )
        if manual:
            merged: dict[str, dict[str, int]] = {
                str(r["bucket"]): {
                    "n_picks": int(r["n_picks"]), "n_hits": int(r["n_hits"]),
                    "total_bet": int(r["total_bet"]), "total_payout": int(r["total_payout"]),
                } for r in stat_rows
            }
            for k, v in manual.items():
                cur = merged.setdefault(
                    k, {"n_picks": 0, "n_hits": 0, "total_bet": 0, "total_payout": 0})
                for f in ("n_picks", "n_hits", "total_bet", "total_payout"):
                    cur[f] += v[f]
            stat_rows = [{"bucket": k, **merged[k]} for k in sorted(merged)]

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
        month_start = from_dt.replace(day=1)
        year_start = from_dt.replace(month=1, day=1)
        pre_rows = (await db.execute(
            text(f"""
                SELECT
                    TO_CHAR(ph.race_date::DATE, 'YYYY-MM')                           AS month_key,
                    COALESCE(SUM(ph.bet_amount), 0)                                   AS total_bet,
                    COALESCE(SUM(CASE WHEN ph.hit = 1 THEN ph.payout ELSE 0 END), 0) AS total_payout
                FROM keirin.picks_history ph
                JOIN keirin.wt_races wr
                  ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
                WHERE ph.race_date >= :year_start AND ph.race_date < :from_date
                {_STATS_COND}
                GROUP BY 1
            """),
            {"year_start": year_start.isoformat(), "from_date": from_dt.isoformat()},
        )).mappings().all()
        for pr in pre_rows:
            mk = str(pr["month_key"])
            bet_v, pay_v = int(pr["total_bet"] or 0), int(pr["total_payout"] or 0)
            yk = mk[:4]
            year_acc.setdefault(yk, {"bet": 0, "payout": 0})
            year_acc[yk]["bet"] += bet_v
            year_acc[yk]["payout"] += pay_v
            if mk >= month_start.strftime("%Y-%m"):
                month_acc.setdefault(mk, {"bet": 0, "payout": 0})
                month_acc[mk]["bet"] += bet_v
                month_acc[mk]["payout"] += pay_v

    for r in stat_rows:
        bucket = str(r["bucket"])
        n_picks = int(r["n_picks"] or 0)
        n_hits = int(r["n_hits"] or 0)
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

    return JSONResponse(content={
        "items": items,
        "period_summary": {
            "n_picks": period_picks,
            "n_hits": period_hits,
            "total_bet": period_bet,
            "total_payout": period_payout,
            "roi": round(period_payout / period_bet, 3) if period_bet > 0 else None,
        },
        # 手動・穴埋め入稿を含めたか。含めた場合、買い目が記録されていなくて
        # 集計から外した件数も返す（黙って落とすと「全入稿」が完全に見える）。
        "include_manual": include_manual,
        "manual_missing_bet_detail": manual_missing,
    })


# netkeirin「ウマい車券」の販売有償ptに対する予想家取り分（2026-08-03・ユーザー提供）。
# 売上金額 = sold_paid_points * NETKEIRIN_REVENUE_RATE。
# 無償pt分は収益にならないため sold_points（総販売pt）ではなく
# **sold_paid_points（有償pt）** に掛けること。
NETKEIRIN_REVENUE_RATE = 0.30


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
    result = {
        "today": await _aggregate(db, "ph.race_date = :d", {"d": today_str}),
        "month": await _aggregate(db, "ph.race_date LIKE :d", {"d": f"{month_prefix}-%"}),
        "year":  await _aggregate(db, "ph.race_date LIKE :d", {"d": f"{year_prefix}-%"}),
        # フロントの「ランク別」展開・絞り込みチップはこの一覧で絞る。
        # 集計側（_aggregate）は既に入稿OFFを除外しているので by_rank には
        # 現れないが、**チップは行が0件でも描かれる**ので明示的に渡す。
        "visible_ranks": await visible_rank_labels(db),
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
STATUS_DELETED = "deleted"


def _payout_range(lines: list[dict]) -> tuple[float | None, float | None]:
    """買い目の **最低払戻 / 最高払戻**（円）。オッズが1つでも欠けたら None。

    🔴 一部だけで計算してはいけない。欠けた点が最安だった場合に
       「最低払戻」を実際より高く見せることになり、確認の役に立たない。
    """
    if not lines or any(x.get("odds") in (None, 0) for x in lines):
        return None, None
    rets = [float(x["stake"]) * float(x["odds"]) for x in lines]
    return min(rets), max(rets)


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
               r.start_at, r.grade, r.race_type, r.n_entries
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
               player_class, prediction_mark, pred_win_pct, pred_top2_pct, pred_top3_pct
        FROM keirin.wt_entries WHERE race_key = ANY(:keys) ORDER BY frame_no
    """), {"keys": keys})).mappings().all()
    by_race: dict[str, list[dict]] = {}
    for e in ent_rows:
        by_race.setdefault(e["race_key"], []).append(dict(e))

    items = []
    for r in rows:
        detail = _parse_bet_detail(r["bet_detail"])
        lines = detail["lines"] if detail else []
        entries = by_race.get(r["race_key"], [])
        top3 = {int(e["frame_no"]): float(e["pred_top3_pct"])
                for e in entries if e["pred_top3_pct"] is not None}
        lo, hi = _payout_range(lines)
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
            "min_payout": lo,
            "max_payout": hi,
            # ガミ＝当たっても投資を下回る。最低払戻が投資額未満なら必ず起きうる
            "gami_risk": (bool(detail and lo is not None and lo < detail["total"])
                          if detail and lo is not None else None),
            "netkeirin_race_id": r["netkeirin_race_id"],
            "proposed_at": r["proposed_at"].isoformat() if r["proposed_at"] else None,
            "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
            "deleted_at": r["deleted_at"].isoformat() if r["deleted_at"] else None,
            "entries": [
                {"frame_no": e["frame_no"], "name": e["name"],
                 "race_point": float(e["race_point"]) if e["race_point"] is not None else None,
                 "style": e["style"], "line_group": e["line_group"],
                 "line_pos": e["line_pos"], "player_class": e["player_class"],
                 "prediction_mark": e["prediction_mark"],
                 "pred_win_pct": float(e["pred_win_pct"]) if e["pred_win_pct"] is not None else None,
                 # 2着内率。列追加（2026-08-12）以降に算出したレースだけ値が入る
                 "pred_top2_pct": float(e["pred_top2_pct"]) if e["pred_top2_pct"] is not None else None,
                 "pred_top3_pct": float(e["pred_top3_pct"]) if e["pred_top3_pct"] is not None else None}
                for e in entries
            ],
        })
    n_proposed = sum(1 for x in items if x["status"] == STATUS_PROPOSED)
    return JSONResponse(content={"date": target, "n_proposed": n_proposed, "items": items})


class ApprovalIn(BaseModel):
    """レース単位（race_key + rank_key）か場単位（date + venue_name）。"""

    race_key: str | None = None
    rank_key: str | None = None
    date: str | None = None
    venue_name: str | None = None
    # 取消専用。netkeirin 側の削除をあきらめて記録だけ取消にする。
    # 既定 False。承認では無視する（keirin 側の CLI も cancel 以外では弾く）。
    force: bool = False
    # 取消専用。date で指定した日の**全場・全件**を対象にする。
    # 🔴 date が無ければ受け付けない（過去分まで巻き込むため）。
    all_venues: bool = False


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
        return await _call_webhook("/approve", {"race_key": base, "rank_key": body.rank_key})
    if body.date and body.venue_name:
        if not _DATE_RE.match(body.date):
            return JSONResponse(content={"ok": False, "message": f"不正な日付: {body.date}"},
                                status_code=400)
        return await _call_webhook("/approve",
                                   {"date": body.date, "venue_name": body.venue_name})
    return JSONResponse(
        content={"ok": False, "message": "race_key+rank_key か date+venue_name が必要です"},
        status_code=400)


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
        return await _call_webhook(
            "/cancel", {"race_key": base, "rank_key": body.rank_key, "force": body.force})

    if body.date and (body.venue_name or body.all_venues):
        if not _DATE_RE.match(body.date):
            return JSONResponse(content={"ok": False, "message": f"不正な日付: {body.date}"},
                                status_code=400)
        payload: dict[str, Any] = {"date": body.date}
        if body.venue_name:
            payload["venue_name"] = body.venue_name
        if body.all_venues:
            payload["all_venues"] = True
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
