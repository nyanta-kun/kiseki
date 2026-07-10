"""競輪 picks/summary API ルーター

keirin スキーマ（PostgreSQL）を参照して結果を返す。

GET /api/keirin/picks?date=YYYY-MM-DD   - 指定日の推奨ピック一覧
GET /api/keirin/summary                  - 当日/当月/当年の投資・回収サマリー
"""
from __future__ import annotations

from datetime import date as Date
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db

_WEBHOOK_BASE = "http://172.18.0.1:8010"

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
    include_all: bool = False,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """指定日（YYYY-MM-DD）の推奨ピック一覧を返す。
    include_all=true の場合は推奨外レースも含む全レースを返す。
    同一レースに複数の購入行（SS=三連複とS/S+=三連単の併存）がある場合は
    行ごとに返す（1レース1行に絞ると片方の的中が一覧から見えなくなるため）。
    """
    target = date or _today_jst().isoformat()

    if include_all:
        rows = (await db.execute(
            text("""
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
                  ph.gap23
                FROM keirin.wt_races wr
                JOIN keirin.venue_info vi
                  ON wr.venue_id = vi.venue_code
                LEFT JOIN keirin.picks_history ph
                  ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
                 AND ph.race_date = :date
                 AND ph.route = 'wt'
                 AND ph.rank != 'GAMI'
                WHERE wr.race_date = :date
                ORDER BY wr.start_at, wr.race_no,
                    CASE ph.rank
                      WHEN '7PLUS_R'    THEN 1
                      WHEN '7PLUS_STP'  THEN 2
                      WHEN '7PLUS_ST'   THEN 3
                      WHEN '7PLUS_CAND' THEN 4
                      ELSE 5
                    END
            """),
            {"date": target},
        )).mappings().all()
    else:
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
                  ph.trio_payout,
                  ph.trifecta_payout,
                  ph.bet_amount,
                  ph.route,
                  COALESCE(ph.miwokuri, FALSE) AS miwokuri,
                  ph.prerace_gami,
                  ph.gap23,
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
                  AND ph.rank != 'GAMI'
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
                  player_class
                FROM keirin.wt_entries
                WHERE race_key = :race_key
                ORDER BY frame_no
            """),
            {"race_key": base_key},
        )).mappings().all()

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
            "pred_combo": r["pred_combo"] if has_pick else None,
            "n_combos": r["n_combos"] if has_pick else None,
            "synth_odds": synth_odds,
            "hit": bool(r["hit"]) if has_pick else False,
            "payout": (r["payout"] or 0) if has_pick else 0,
            "trio_payout": (r["trio_payout"] or 0) if has_pick else 0,
            "trifecta_payout": (r["trifecta_payout"] or 0) if has_pick else 0,
            "bet_amount": (r["bet_amount"] or 0) if has_pick else 0,
            "miwokuri": bool(r["miwokuri"]) if has_pick else False,
            "prerace_gami": float(r["prerace_gami"]) if (has_pick and r["prerace_gami"] is not None) else None,
            "gap23": float(r["gap23"]) if (has_pick and r.get("gap23") is not None) else None,
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
    roi = round(total_payout / total_bet, 3) if total_bet > 0 else None
    return {
        "n_picks": n_picks,
        "n_hits": n_hits,
        "total_bet": total_bet,
        "total_payout": total_payout,
        "roi": roi,
    }


_SETTLED_COND = """(
    wr.status = 3
    OR (wr.start_at IS NOT NULL AND wr.start_at::BIGINT + 5400 < EXTRACT(EPOCH FROM NOW()))
)"""


async def _aggregate(
    db: AsyncSession,
    where: str,
    params: dict[str, Any],
) -> dict:
    row = (await db.execute(
        text(f"""
            SELECT
              COUNT(*)                                                          AS n_picks,
              SUM(ph.hit)                                                       AS n_hits,
              COALESCE(SUM(ph.bet_amount), 0)                                   AS total_bet,
              COALESCE(SUM(CASE WHEN ph.hit = 1 THEN ph.payout ELSE 0 END), 0) AS total_payout
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE {where}
              AND NOT COALESCE(ph.miwokuri, FALSE)
              AND ph.bet_amount > 0
              AND ph.rank IN ('7PLUS_R', '7PLUS_ST', '7PLUS_STP')
              AND ph.race_key NOT LIKE '%#CAND'
              AND {_SETTLED_COND}
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
              ph.rank                                                            AS rank,
              COUNT(*)                                                           AS n_picks,
              SUM(ph.hit)                                                        AS n_hits,
              COALESCE(SUM(ph.bet_amount), 0)                                    AS total_bet,
              COALESCE(SUM(CASE WHEN ph.hit = 1 THEN ph.payout ELSE 0 END), 0)  AS total_payout
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE {where}
              AND NOT COALESCE(ph.miwokuri, FALSE)
              AND ph.bet_amount > 0
              AND ph.rank IN ('7PLUS_R', '7PLUS_ST', '7PLUS_STP')
              AND ph.race_key NOT LIKE '%#CAND'
              AND {_SETTLED_COND}
            GROUP BY ph.rank
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


@router.post("/refresh")
async def refresh_picks(
    date: str = "",
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """指定日の #CAND エントリを採点して確定エントリに変換する。

    VPS cron が失敗した場合のフォールバック。
    wt_entries の finish_order と wt_odds の払戻を元に採点する。
    結果未確定レース（finish_order 不足）はスキップ。
    """
    import re as _re
    target = date or _today_jst().isoformat()

    cand_rows = (await db.execute(
        text("""
            SELECT race_key, rank, pred_combo, n_combos, miwokuri, prerace_gami
            FROM keirin.picks_history
            WHERE race_date = :date
              AND race_key LIKE '%#CAND'
              AND route = 'wt'
        """),
        {"date": target},
    )).mappings().all()

    if not cand_rows:
        return JSONResponse(content={"message": "採点対象の#CANDレコードがありません", "n_scored": 0})

    to_delete: list[str] = []
    to_insert: list[dict] = []

    for row in cand_rows:
        base_key = row["race_key"].rsplit("#", 1)[0]
        rank = row["rank"]
        pred_combo = row["pred_combo"] or ""

        store_key = (
            f"{base_key}#7SS" if rank == "7PLUS_SS"
            else f"{base_key}#7S" if rank == "7PLUS_S"
            else f"{base_key}#7R" if rank == "7PLUS_R"
            else f"{base_key}#7A"
        )

        try:
            parts = pred_combo.split("-")
            p1, p2 = int(parts[0]), int(parts[1])
            thirds = [int(x) for x in parts[2].split(",")] if len(parts) >= 3 else []
        except (ValueError, IndexError):
            continue

        finish_rows = (await db.execute(
            text("""
                SELECT frame_no FROM keirin.wt_entries
                WHERE race_key = :rk AND finish_order BETWEEN 1 AND 3
                ORDER BY finish_order
            """),
            {"rk": base_key},
        )).fetchall()

        order_list = [int(r[0]) for r in finish_rows]
        if len(order_list) < 3:
            continue

        top3 = frozenset(order_list[:3])

        runner_rows = (await db.execute(
            text("SELECT frame_no FROM keirin.wt_entries WHERE race_key = :rk AND finish_order >= 1"),
            {"rk": base_key},
        )).fetchall()
        runners = {int(r[0]) for r in runner_rows}

        if p1 not in runners or p2 not in runners:
            continue
        valid_thirds = [t for t in thirds if t in runners]
        if not valid_thirds:
            continue

        odds_rows = (await db.execute(
            text("""
                SELECT combination, odds_value
                FROM keirin.wt_odds
                WHERE race_key = :rk AND bet_type = 'trio'
            """),
            {"rk": base_key},
        )).mappings().all()

        trio_map: dict = {}
        for odds_row in odds_rows:
            try:
                nums = [int(p) for p in _re.split(r"[-=→]", str(odds_row["combination"])) if p]
                # 公式払戻金は10円単位に切り捨て。round()で浮動小数点誤差を吸収してから10円に丸める
                trio_map[frozenset(nums)] = round(float(odds_row["odds_value"]) * 100) // 10 * 10
            except (ValueError, TypeError):
                continue

        trifecta_rows = (await db.execute(
            text("""
                SELECT combination, odds_value
                FROM keirin.wt_odds
                WHERE race_key = :rk AND bet_type = 'trifecta'
                  AND combination = :combo
            """),
            {"rk": base_key, "combo": "-".join(map(str, order_list[:3]))},
        )).mappings().all()
        trifecta_pay = 0
        if trifecta_rows and trifecta_rows[0]["odds_value"]:
            trifecta_pay = round(float(trifecta_rows[0]["odds_value"]) * 100) // 10 * 10

        hit = False
        pay = 0
        for t in valid_thirds:
            key = frozenset((p1, p2, t))
            if key == top3:
                pay = trio_map.get(key, 0)
                hit = True
                break

        trio_pay = trio_map.get(top3, 0)
        prerace_gami = row["prerace_gami"]
        # SS（旧カット方式・過去日互換）はガミ目カット済みのためgami判定不適用。
        # S（過去日互換）/ R（2026-07-10〜 レース単位・全目min≥7.0）は閾値7.0で見送り判定。
        is_gami_skip = rank != "7PLUS_SS" and prerace_gami is not None and float(prerace_gami) < 7.0

        is_skip = bool(row["miwokuri"]) or is_gami_skip
        to_delete.append(row["race_key"])
        to_insert.append({
            "race_date": target,
            "race_key": store_key,
            "rank": rank,
            "pred_combo": pred_combo,
            "n_combos": row["n_combos"] or 0,
            "hit": 1 if hit else 0,
            "payout": 0 if is_skip else (pay if hit else 0),
            "trio_payout": trio_pay,
            "trifecta_payout": trifecta_pay,
            "bet_amount": 0 if is_skip else (row["n_combos"] or 0) * 100,
            "miwokuri": is_skip,
            "prerace_gami": float(prerace_gami) if prerace_gami is not None else None,
        })

    if not to_insert:
        return JSONResponse(content={"message": "採点対象レースの確定データがありません", "n_scored": 0})

    await db.execute(
        text("DELETE FROM keirin.picks_history WHERE race_key = ANY(:keys)"),
        {"keys": to_delete},
    )
    for s in to_insert:
        await db.execute(
            text("""
                INSERT INTO keirin.picks_history
                    (race_date, race_key, rank, pred_combo, n_combos, hit, payout, trio_payout, trifecta_payout,
                     bet_amount, route, miwokuri, prerace_gami)
                VALUES
                    (:race_date, :race_key, :rank, :pred_combo, :n_combos, :hit, :payout,
                     :trio_payout, :trifecta_payout, :bet_amount, 'wt', :miwokuri, :prerace_gami)
                ON CONFLICT (race_key) DO UPDATE SET
                    hit = EXCLUDED.hit,
                    payout = EXCLUDED.payout,
                    trio_payout = EXCLUDED.trio_payout,
                    rank = EXCLUDED.rank
            """),
            s,
        )
    await db.commit()

    n_hits = sum(1 for s in to_insert if s["hit"])
    total_payout = sum(s["payout"] for s in to_insert)
    return JSONResponse(content={
        "n_scored": len(to_insert),
        "n_hits": n_hits,
        "total_payout": total_payout,
        "message": f"{len(to_insert)}件採点完了 (的中{n_hits}件)",
    })


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


@router.get("/stats")
async def get_stats(
    from_date: str = "",
    to_date: str = "",
    granularity: str = "daily",
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """日別 / 月別の投資・回収・累積ROI推移を返す。

    granularity: "daily"（日別）または "monthly"（月別）
    from_date / to_date: YYYY-MM-DD 形式。省略時は直近30日。
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

    _STATS_COND = """
        AND NOT COALESCE(ph.miwokuri, FALSE)
        AND ph.bet_amount > 0
        AND ph.rank IN ('7PLUS_R', '7PLUS_ST', '7PLUS_STP')
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
        "today": await _aggregate(db, "ph.race_date = :d", {"d": today_str}),
        "month": await _aggregate(db, "ph.race_date LIKE :d", {"d": f"{month_prefix}-%"}),
        "year":  await _aggregate(db, "ph.race_date LIKE :d", {"d": f"{year_prefix}-%"}),
        "test":       model_eval,
        "test_from":  model_eval.get("period_from"),
        "test_to":    model_eval.get("period_to"),
    }

    return JSONResponse(content=result)
