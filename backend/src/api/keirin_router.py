"""競輪 picks/summary API ルーター

keirin スキーマ（PostgreSQL）を参照して結果を返す。

GET /api/keirin/picks?date=YYYY-MM-DD   - 指定日の推奨ピック一覧
GET /api/keirin/summary                  - 当日/当月/当年の投資・回収サマリー
"""
from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db

_JST = timezone(timedelta(hours=9))


def _today_jst() -> Date:
    return datetime.now(_JST).date()

router = APIRouter(prefix="/api/keirin", tags=["keirin"])


# ---------------------------------------------------------------------------
# 合成オッズ計算
# ---------------------------------------------------------------------------

def _parse_combinations(pred_combo: str | None, is_wide: bool) -> list[str]:
    """pred_combo 文字列を個別組み合わせキーのリストに変換する。

    - WIDE: '4-2' → ['2-4'] (車番を昇順にソート)
    - 3連単: '1-4-3,5,2' → ['1-4-3', '1-4-5', '1-4-2']
    """
    if not pred_combo:
        return []
    parts = pred_combo.split("-")
    if is_wide and len(parts) == 2:
        a, b = sorted([parts[0].strip(), parts[1].strip()], key=int)
        return [f"{a}-{b}"]
    if len(parts) >= 2:
        axis1, axis2 = parts[0].strip(), parts[1].strip()
        thirds = parts[2].split(",") if len(parts) > 2 else []
        if thirds:
            return [f"{axis1}-{axis2}-{t.strip()}" for t in thirds]
        return [f"{axis1}-{axis2}"]
    return []


async def _calc_synth_odds(
    db: AsyncSession,
    race_key: str,
    pred_combo: str | None,
    is_wide: bool,
) -> float | None:
    """朝オッズから合成オッズ（= 1 / Σ(1/odds)）を計算して返す。データ不足時は None。"""
    combos = _parse_combinations(pred_combo, is_wide)
    if not combos:
        return None

    bet_type = "quinella" if is_wide else "trifecta"
    rows = (await db.execute(
        text("""
            SELECT combination, odds_value
            FROM keirin.wt_odds_snapshot
            WHERE race_key = :rk
              AND bet_type = :bt
              AND combination = ANY(:combos)
              AND snapshot_type = 'morning'
        """),
        {"rk": race_key, "bt": bet_type, "combos": combos},
    )).mappings().all()

    odds_map = {r["combination"]: r["odds_value"] for r in rows if r["odds_value"]}
    matched = [odds_map[c] for c in combos if c in odds_map]
    if not matched:
        return None

    return round(1.0 / sum(1.0 / o for o in matched), 2)


# ---------------------------------------------------------------------------
# picks
# ---------------------------------------------------------------------------

@router.get("/picks")
async def get_picks(
    date: str = "",
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """指定日（YYYY-MM-DD）の推奨ピック一覧を返す。"""
    target = date or _today_jst().isoformat()

    rows = (await db.execute(
        text("""
            SELECT
              ph.id,
              ph.race_key,
              SPLIT_PART(ph.race_key, '#', 1) AS base_key,
              ph.rank,
              ph.pred_combo,
              ph.n_combos,
              ph.hit,
              ph.payout,
              ph.bet_amount,
              ph.route,
              COALESCE(ph.miwokuri, FALSE) AS miwokuri,
              ph.prerace_gami,
              wr.race_no,
              wr.grade,
              wr.race_type,
              wr.start_at,
              wr.status,
              vi.name AS venue_name
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            JOIN keirin.venue_info vi
              ON wr.venue_id = vi.venue_code
            WHERE ph.race_date = :date
            ORDER BY wr.start_at, ph.id
        """),
        {"date": target},
    )).mappings().all()

    picks = []
    for r in rows:
        base_key = r["base_key"]
        is_wide = r["rank"] == "WIDE"
        synth_odds = await _calc_synth_odds(db, base_key, r["pred_combo"], is_wide)

        entries = (await db.execute(
            text("""
                SELECT
                  frame_no,
                  name,
                  race_point,
                  style,
                  line_pos,
                  finish_order,
                  player_class
                FROM keirin.wt_entries
                WHERE race_key = :race_key
                ORDER BY frame_no
            """),
            {"race_key": base_key},
        )).mappings().all()

        picks.append({
            "id": r["id"],
            "race_key": r["race_key"],
            "venue_name": r["venue_name"],
            "race_no": r["race_no"],
            "grade": r["grade"],
            "race_type": r["race_type"],
            "start_at": r["start_at"],
            "status": r["status"],
            "rank": r["rank"],
            "pred_combo": r["pred_combo"],
            "n_combos": r["n_combos"],
            "synth_odds": synth_odds,
            "hit": bool(r["hit"]),
            "payout": r["payout"] or 0,
            "bet_amount": r["bet_amount"] or 0,
            "miwokuri": bool(r["miwokuri"]),
            "prerace_gami": float(r["prerace_gami"]) if r["prerace_gami"] is not None else None,
            "entries": [
                {
                    "frame_no": e["frame_no"],
                    "name": e["name"],
                    "race_point": e["race_point"],
                    "style": e["style"],
                    "line_pos": e["line_pos"],
                    "finish_order": e["finish_order"],
                    "player_class": e["player_class"],
                }
                for e in entries
            ],
        })

    return JSONResponse(content=picks)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def _make_period_dict(n_picks: int, n_hits: int, total_bet: int, total_payout: int) -> dict:
    roi = round(total_payout / total_bet, 3) if (total_bet > 0 and total_payout > 0) else None
    return {
        "n_picks": n_picks,
        "n_hits": n_hits,
        "total_bet": total_bet,
        "total_payout": total_payout,
        "roi": roi,
    }


async def _aggregate(
    db: AsyncSession,
    where: str,
    params: dict[str, Any],
) -> dict:
    row = (await db.execute(
        text(f"""
            SELECT
              COUNT(*)                                                  AS n_picks,
              SUM(hit)                                                  AS n_hits,
              COALESCE(SUM(bet_amount), 0)                              AS total_bet,
              COALESCE(SUM(CASE WHEN hit = 1 THEN payout ELSE 0 END), 0) AS total_payout
            FROM keirin.picks_history
            WHERE {where}
              AND NOT COALESCE(miwokuri, FALSE)
              AND rank IN ('7PLUS_SS', '7PLUS_S', '7PLUS_A')
              AND (prerace_gami IS NULL OR prerace_gami >= 5.0)
        """),
        params,
    )).mappings().one_or_none()

    if not row:
        return {"n_picks": 0, "n_hits": 0, "total_bet": 0, "total_payout": 0, "roi": None, "by_rank": {}}

    n_picks = int(row["n_picks"] or 0)
    n_hits = int(row["n_hits"] or 0)
    total_bet = int(row["total_bet"] or 0)
    total_payout = int(row["total_payout"] or 0)
    result = _make_period_dict(n_picks, n_hits, total_bet, total_payout)

    # ランク別集計
    rank_rows = (await db.execute(
        text(f"""
            SELECT
              rank,
              COUNT(*)                                                  AS n_picks,
              SUM(hit)                                                  AS n_hits,
              COALESCE(SUM(bet_amount), 0)                              AS total_bet,
              COALESCE(SUM(CASE WHEN hit = 1 THEN payout ELSE 0 END), 0) AS total_payout
            FROM keirin.picks_history
            WHERE {where}
              AND NOT COALESCE(miwokuri, FALSE)
              AND rank IN ('7PLUS_SS', '7PLUS_S', '7PLUS_A')
              AND (prerace_gami IS NULL OR prerace_gami >= 5.0)
            GROUP BY rank
        """),
        params,
    )).mappings().all()

    by_rank: dict[str, dict] = {}
    for r in rank_rows:
        key = str(r["rank"]).replace("7PLUS_", "")
        by_rank[key] = _make_period_dict(
            int(r["n_picks"] or 0),
            int(r["n_hits"] or 0),
            int(r["total_bet"] or 0),
            int(r["total_payout"] or 0),
        )
    result["by_rank"] = by_rank
    return result


async def _get_model_eval(db: AsyncSession, period_type: str = "HOLD") -> dict:
    """keirin.model_evaluation から最新のバックテスト結果を返す。
    ランク別行（model_name に '#7SS'/'#7S'/'#7A' サフィックス付き）も by_rank に含める。
    """
    row = (await db.execute(
        text("""
            SELECT n_picks, n_hits, total_bet, total_payout, roi,
                   period_from, period_to
            FROM keirin.model_evaluation
            WHERE period_type = :pt
              AND model_name NOT LIKE '%#7%'
            ORDER BY evaluated_at DESC
            LIMIT 1
        """),
        {"pt": period_type},
    )).mappings().one_or_none()

    if not row:
        return {"n_picks": 0, "n_hits": 0, "total_bet": 0, "total_payout": 0, "roi": None,
                "period_from": None, "period_to": None, "by_rank": {}}

    roi_val = float(row["roi"]) if row["roi"] is not None else None
    result = {
        "n_picks":      int(row["n_picks"] or 0),
        "n_hits":       int(row["n_hits"] or 0),
        "total_bet":    int(row["total_bet"] or 0),
        "total_payout": int(row["total_payout"] or 0),
        "roi":          round(roi_val, 3) if roi_val is not None else None,
        "period_from":  row["period_from"],
        "period_to":    row["period_to"],
    }

    # ランク別行を取得（model_name サフィックスで識別）
    rank_rows = (await db.execute(
        text("""
            SELECT model_name, n_picks, n_hits, total_bet, total_payout, roi
            FROM keirin.model_evaluation
            WHERE period_type = :pt
              AND model_name LIKE '%#7%'
            ORDER BY evaluated_at DESC
        """),
        {"pt": period_type},
    )).mappings().all()

    # 同一 evaluated_at の最新セットのみ使用（ランクキーに重複があれば最新を優先）
    by_rank: dict[str, dict] = {}
    for r in rank_rows:
        suffix = str(r["model_name"]).rsplit("#", 1)[-1]  # "7SS" / "7S" / "7A"
        rank_key = suffix.replace("7", "", 1) if suffix.startswith("7") else suffix
        if rank_key not in by_rank:
            by_rank[rank_key] = _make_period_dict(
                int(r["n_picks"] or 0),
                int(r["n_hits"] or 0),
                int(r["total_bet"] or 0),
                int(r["total_payout"] or 0),
            )

    result["by_rank"] = by_rank
    return result


@router.get("/summary")
async def get_summary(date: str = "", db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """当日 / 当月 / 当年 / HOLD期間バックテストのサマリーを返す。
    date（YYYY-MM-DD）を指定するとその日付を基準に当日/当月/当年を集計する。
    test フィールドは keirin.model_evaluation の最新 HOLD 期間評価を使用する。
    """
    try:
        today = Date.fromisoformat(date) if date else _today_jst()
    except ValueError:
        today = _today_jst()
    today_str = today.isoformat()
    month_prefix = today.strftime("%Y-%m")
    year_prefix = str(today.year)

    model_eval = await _get_model_eval(db, period_type="HOLD")

    result = {
        "today": await _aggregate(db, "race_date = :d", {"d": today_str}),
        "month": await _aggregate(db, "race_date LIKE :d", {"d": f"{month_prefix}-%"}),
        "year":  await _aggregate(db, "race_date LIKE :d", {"d": f"{year_prefix}-%"}),
        "test":       model_eval,
        "test_from":  model_eval.get("period_from"),
        "test_to":    model_eval.get("period_to"),
    }

    return JSONResponse(content=result)
