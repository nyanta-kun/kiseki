"""競輪 picks/summary API ルーター

keirin プロジェクトの SQLite DB を直読みして結果を返す。
KEIRIN_DB_PATH が未設定または DB ファイルが存在しない場合は 503 を返す。

GET /api/keirin/picks?date=YYYY-MM-DD   - 指定日の推奨ピック一覧
GET /api/keirin/summary                  - 当日/当月/当年の投資・回収サマリー
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date as Date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ..config import settings

router = APIRouter(prefix="/api/keirin", tags=["keirin"])


@contextmanager
def _connect():
    """keirin SQLite に接続し、終了時に閉じる。DB 未設定なら RuntimeError。"""
    path = settings.keirin_db_path
    if not path or not Path(path).exists():
        raise RuntimeError("keirin DB unavailable")
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _db_or_503():
    """DB が使えない場合は 503 を返すためのガード。"""
    path = settings.keirin_db_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=503, detail="keirin DB not configured")


# ---------------------------------------------------------------------------
# picks
# ---------------------------------------------------------------------------

@router.get("/picks")
def get_picks(date: str = Query(default="")) -> JSONResponse:
    """指定日（YYYY-MM-DD）の推奨ピック一覧を返す。"""
    _db_or_503()
    target = date or Date.today().isoformat()

    try:
        with _connect() as conn:
            # picks + race info
            rows = conn.execute(
                """
                SELECT
                  ph.id,
                  ph.race_key,
                  REPLACE(ph.race_key, '#W', '') AS base_key,
                  ph.rank,
                  ph.pred_combo,
                  ph.n_combos,
                  ph.hit,
                  ph.payout,
                  ph.bet_amount,
                  ph.route,
                  wr.race_no,
                  wr.grade,
                  wr.race_type,
                  wr.start_at,
                  wr.status,
                  vi.name AS venue_name
                FROM picks_history ph
                JOIN wt_races wr
                  ON REPLACE(ph.race_key, '#W', '') = wr.race_key
                JOIN venue_info vi
                  ON wr.venue_id = vi.venue_code
                WHERE ph.race_date = ?
                ORDER BY wr.start_at, ph.id
                """,
                (target,),
            ).fetchall()

            picks = []
            for r in rows:
                base_key = r["base_key"]
                # エントリー（車番・名前・指数・スタイル・着順）
                entries = conn.execute(
                    """
                    SELECT
                      frame_no,
                      name,
                      race_point,
                      style,
                      line_pos,
                      finish_order,
                      player_class
                    FROM wt_entries
                    WHERE race_key = ?
                    ORDER BY frame_no
                    """,
                    (base_key,),
                ).fetchall()

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
                    "hit": bool(r["hit"]),
                    "payout": r["payout"],
                    "bet_amount": r["bet_amount"],
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
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return JSONResponse(content=picks)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def _aggregate(conn: sqlite3.Connection, where: str, params: tuple[Any, ...]) -> dict:
    row = conn.execute(
        f"""
        SELECT
          COUNT(*)                                             AS n_picks,
          SUM(hit)                                            AS n_hits,
          COALESCE(SUM(bet_amount), 0)                        AS total_bet,
          COALESCE(SUM(CASE WHEN hit THEN payout ELSE 0 END), 0) AS total_payout
        FROM picks_history
        WHERE {where}
        """,
        params,
    ).fetchone()
    if not row:
        return {"n_picks": 0, "n_hits": 0, "total_bet": 0, "total_payout": 0, "roi": None}
    n_picks = row["n_picks"] or 0
    n_hits = row["n_hits"] or 0
    total_bet = row["total_bet"] or 0
    total_payout = row["total_payout"] or 0
    roi = round(total_payout / total_bet, 3) if total_bet > 0 else None
    return {
        "n_picks": n_picks,
        "n_hits": n_hits,
        "total_bet": total_bet,
        "total_payout": total_payout,
        "roi": roi,
    }


@router.get("/summary")
def get_summary() -> JSONResponse:
    """当日 / 当月 / 当年の投資・回収サマリーを返す。"""
    _db_or_503()

    today = Date.today()
    today_str = today.isoformat()          # YYYY-MM-DD
    month_prefix = today.strftime("%Y-%m") # YYYY-MM
    year_prefix = str(today.year)          # YYYY

    try:
        with _connect() as conn:
            result = {
                "today": _aggregate(conn, "race_date = ?", (today_str,)),
                "month": _aggregate(conn, "race_date LIKE ?", (f"{month_prefix}-%",)),
                "year": _aggregate(conn, "race_date LIKE ?", (f"{year_prefix}-%",)),
            }
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return JSONResponse(content=result)
