"""競輪 picks/summary API ルーター

keirin スキーマ（PostgreSQL）を参照して結果を返す。

GET /api/keirin/picks?date=YYYY-MM-DD   - 指定日の推奨ピック一覧
GET /api/keirin/summary                  - 当日/当月/当年の投資・回収サマリー
"""
from __future__ import annotations

import math
import re
from datetime import date as Date
from datetime import datetime, timedelta, timezone
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
from .import_router import ApiKeyDep

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
# （別リポジトリのため import 不可・以下の辞書へ手動で複製する）。
# _VALID_PICK_RANKS / _RANKS_ALL / _display_rank() は全てこの辞書から導出し、
# ランク名の二重管理を避ける（新ランクの追加/廃止時は _PAPER_RANK_LABELS の
# みを更新すればよい設計とする。同じ名前を複数箇所にハードコードし直す運用が
# 今回の障害の一因だったため）。
#
# 全廃済み（picks_history に残っていても表示・集計対象から除外する残骸）:
#   SEVEN_S1（win軸1着固定×3着内モデル相手2車・三連単2点流し。2026-07-31全廃）
#   SIX_S1 / 7PLUS_R / 7PLUS_U / 7PLUS_M / 7PLUS_ST / 7PLUS_STP（いずれも既に全廃済み）
# ---------------------------------------------------------------------------
# 定義順 = Web 全体の表示順（7SS/7S/7A/9S/9A。ユーザー指定・2026-08-01）。
_PAPER_RANK_LABELS: dict[str, str] = {
    "RANK_7SS": "7SS",
    "RANK_7S": "7S",
    "RANK_7A": "7A",
    "RANK_9S": "9S",
    "RANK_9A": "9A",
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
                  ph.race_key                AS ph_race_key,
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
                  ph.gate_label
                FROM keirin.wt_races wr
                JOIN keirin.venue_info vi
                  ON wr.venue_id = vi.venue_code
                LEFT JOIN keirin.picks_history ph
                  ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
                 AND ph.race_date = :date
                 AND ph.route = 'wt'
                 AND ph.rank IN {_VALID_PICK_RANKS}
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
                ORDER BY wr.start_at, ph.id
            """),
            {"date": target},
        )).mappings().all()

    picks = []
    for r in rows:
        base_key = r["base_key"]
        has_pick = r["rank"] is not None

        if has_pick:
            is_wide = r["rank"] == "WIDE"
            race_key = r["ph_race_key"] if include_all else r["race_key"]
            synth_odds = await _calc_synth_odds(db, base_key, r["pred_combo"], is_wide)
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
                  finish_order,
                  player_class,
                  pred_win_pct,
                  pred_top3_pct,
                  prediction_mark
                FROM keirin.wt_entries
                WHERE race_key = :race_key
                ORDER BY frame_no
            """),
            {"race_key": base_key},
        )).mappings().all()

        # 推奨外レース・採点前の候補行でも、レース確定後は三連複/三連単の払戻を表示する。
        # picks_history に未記録（0円）の場合は wt_odds の最終オッズ×100 から算出
        # （10円単位切り捨て。実払戻との一致は 2026-07-12 に検証済み）。
        trio_pay = int(r["trio_payout"] or 0) if has_pick else 0
        trifecta_pay = int(r["trifecta_payout"] or 0) if has_pick else 0
        if trio_pay == 0 or trifecta_pay == 0:
            top3 = sorted(
                (e for e in entries
                 if e["finish_order"] is not None and 1 <= e["finish_order"] <= 3),
                key=lambda e: e["finish_order"],
            )
            if len(top3) == 3:
                frames = [int(e["frame_no"]) for e in top3]
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
            "start_at": r["start_at"],
            "status": r["status"],
            "n_entries": r["n_entries"],
            "rank": r["rank"],
            "display_rank": _display_rank(str(r["rank"])) if has_pick else None,
            "pred_combo": r["pred_combo"] if has_pick else None,
            "n_combos": r["n_combos"] if has_pick else None,
            "synth_odds": synth_odds,
            "hit": bool(r["hit"]) if has_pick else False,
            "payout": (r["payout"] or 0) if has_pick else 0,
            "trio_payout": trio_pay,
            "trifecta_payout": trifecta_pay,
            "bet_amount": (r["bet_amount"] or 0) if has_pick else 0,
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
            "entries": [
                {
                    "frame_no": e["frame_no"],
                    "name": e["name"],
                    "race_point": e["race_point"],
                    "style": e["style"],
                    "line_pos": e["line_pos"],
                    "finish_order": e["finish_order"],
                    "player_class": e["player_class"],
                    "pred_win_pct": float(e["pred_win_pct"]) if e["pred_win_pct"] is not None else None,
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
# 2026-08-01、RANK_7SS（新設の独立ランク・波乱軸選出/穴レース検知）を追加＝
# ユーザー要望「7SS の追加を行ったため VPS の 7SS 表示も有効にして下さい」への
# 対応）。by_rank（_aggregate内部で_display_rank()により算出）にはこれら全ランク
# が同じ辞書に並ぶため、フロントエンドの「ランク別」展開でまとめて確認できる。
_RANKS_ALL = "(" + ", ".join(f"'{r}'" for r in _PAPER_RANK_LABELS) + ")"


async def _aggregate(
    db: AsyncSession,
    where: str,
    params: dict[str, Any],
    rank_filter: str = _RANKS_ALL,
) -> dict:
    # 2026-08-01〜: 現行ランクは _PAPER_RANK_LABELS の5ランク（RANK_7S/RANK_7A/
    # RANK_9S/RANK_9A/RANK_7SS）。gate_labelによる表示分岐は廃止済み（_display_rank
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
        """),
        params,
    )).mappings().one_or_none()
    result["n_candidates"] = int(cand_row["n_candidates"] or 0) if cand_row else 0

    # ランク別集計（全てペーパー・名目賭金）: RANK_7S/RANK_7A/RANK_9S/RANK_9A/RANK_7SS の5ランク。
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
async def refresh_picks(date: str = "") -> JSONResponse:
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
async def trigger_fetch_odds() -> JSONResponse:
    """発走前ガミ判定を即時実行する（keirinホスト側スクリプトをバックグラウンド起動）。"""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_WEBHOOK_BASE}/fetch-odds", timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse(content={"ok": False, "message": str(exc)}, status_code=503)


@router.post("/fetch-results")
async def trigger_fetch_results() -> JSONResponse:
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
async def trigger_submit_race(body: SubmitRaceIn) -> JSONResponse:
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
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """日別 / 月別の投資・回収・累積ROI推移を返す。

    granularity: "daily"（日別）または "monthly"（月別）
    from_date / to_date: YYYY-MM-DD 形式。省略時は直近30日。
    rank: 集計対象ランク。カンマ区切りで複数指定可（例: "7SS,9S"）。
          "7S"（RANK_7S）/ "7A"（RANK_7A・境界ランク）/ "9S"（RANK_9S）/
          "9A"（RANK_9A・境界ランク）/ "7SS"（RANK_7SS・波乱軸選出/穴レース検知、
          2026-07-31新設の独立ランク）/ "all"（既定値・全ランク合算。
          トップライン=/summaryと揃える）。
          2026-08-01〜: gate_label('SS'/'S')による7SS/9SSの分岐は
          keirin側commit e994758（2026-07-31）で廃止済みのため、
          "7SS" は上記の新設独立ランクのみを指す（旧"9SS"は廃止・対象外）。
          "all" が含まれる、または未知の値のみの場合は全体扱いにフォールバックする。
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
    # OR条件として結合する（例: "7SS,9S" → RANK_7SS or RANK_9S）。
    # 2026-08-01〜: gate_labelによる分岐は廃止済み・内部rankは_PAPER_RANK_LABELSの
    # 5ランクへ全面改名済みのため、それぞれ単純な等価条件になる。
    # 既定の"all"は全ランクをまとめて集計する（/summaryと同じ方針）。
    _RANK_COND_MAP = {
        "7SS": "ph.rank = 'RANK_7SS'",
        "7S": "ph.rank = 'RANK_7S'",
        "7A": "ph.rank = 'RANK_7A'",
        "9S": "ph.rank = 'RANK_9S'",
        "9A": "ph.rank = 'RANK_9A'",
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

    for r in rows:
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
    }

    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# netkeirin（ウマい車券）自動入稿設定
# ---------------------------------------------------------------------------

# 表示ランク一覧（_display_rank()の出力と一致・7SS/7S/7A/9S/9A）。
# 並び順は Web 全体で 7SS/7S/7A/9S/9A に統一（ユーザー指定・2026-08-01。
# frontend の RANK_ORDER / RANK_FILTERS と同一基準）。
# '_global' は全体ON/OFFを表す特殊行。
# 2026-08-01〜: S1（2026-07-31全廃）・9SS（gate_label分岐廃止に伴い消滅）は
# 対象外。DBには過去分のnetkeirin_settings行（rank_key='S1'/'9SS'、いずれも
# enabled=false）が残るが、新規保存時のバリデーション対象からは外す
# （フロントエンド側もこれらを画面に表示しない）。
NETKEIRIN_RANK_KEYS = ("_global", "7SS", "7S", "7A", "9S", "9A")


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
