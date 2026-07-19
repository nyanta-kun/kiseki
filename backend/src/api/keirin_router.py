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
                  ph.gap12,
                  ph.gap23,
                  ph.gap34
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
                      WHEN '7PLUS_CAND' THEN 2
                      ELSE 3
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
                  ph.gap12,
                  ph.gap23,
                  ph.gap34,
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
                  player_class,
                  pred_win_pct,
                  pred_top3_pct
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
            "trio_payout": trio_pay,
            "trifecta_payout": trifecta_pay,
            "bet_amount": (r["bet_amount"] or 0) if has_pick else 0,
            "miwokuri": bool(r["miwokuri"]) if has_pick else False,
            "prerace_gami": float(r["prerace_gami"]) if (has_pick and r["prerace_gami"] is not None) else None,
            "gap12": float(r["gap12"]) if (has_pick and r.get("gap12") is not None) else None,
            "gap23": float(r["gap23"]) if (has_pick and r.get("gap23") is not None) else None,
            "gap34": float(r["gap34"]) if (has_pick and r.get("gap34") is not None) else None,
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

# 内部rank → by_rank キー（表示ランク）のマッピング
_RANK_KEY_MAP = {
    "SEVEN_S1": "S1",  # 表示 S1（2026-07-19 新設計: win軸1着固定×3着内モデル相手2車）
    "7PLUS_U": "U",   # 表示 S2
    "7PLUS_M": "M",   # 表示 S3（2026-07-17 新定義: 不一致×gap12≥0.10）
}


async def _aggregate(
    db: AsyncSession,
    where: str,
    params: dict[str, Any],
) -> dict:
    # 2026-07-19〜: 現行ランクは S1/S2/S3 の3ペーパー（旧新S1=SIX_S1 / A=7PLUS_A は全廃・
    # 行はアーカイブ退避済み）。トップラインは3ランクの名目合算。
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
              AND ph.rank IN ('SEVEN_S1', '7PLUS_U', '7PLUS_M')
              AND ph.race_key NOT LIKE '%#CAND'
              AND {_SETTLED_COND}
        """),
        params,
    )).mappings().one_or_none()

    if not row:
        return {"n_picks": 0, "n_hits": 0, "total_bet": 0, "total_payout": 0, "roi": None,
                "n_candidates": 0, "by_rank": {}}

    n_picks = int(row["n_picks"] or 0)
    n_hits = int(row["n_hits"] or 0)
    total_bet = int(row["total_bet"] or 0)
    total_payout = int(row["total_payout"] or 0)
    result = _make_period_dict(n_picks, n_hits, total_bet, total_payout)

    # 総候補レース数（判定前候補+見送り含む・2ペーパーランクの distinct レース数）
    cand_row = (await db.execute(
        text(f"""
            SELECT COUNT(DISTINCT SPLIT_PART(ph.race_key, '#', 1)) AS n_candidates
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE {where}
              AND ph.route = 'wt'
              AND ph.rank IN ('SEVEN_S1', '7PLUS_U', '7PLUS_M')
              AND {_SETTLED_COND}
        """),
        params,
    )).mappings().one_or_none()
    result["n_candidates"] = int(cand_row["n_candidates"] or 0) if cand_row else 0

    # ランク別集計（全てペーパー・名目賭金）: S1=SEVEN_S1 / S2=7PLUS_U / S3=7PLUS_M
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
              AND ph.rank IN ('SEVEN_S1', '7PLUS_U', '7PLUS_M')
              AND ph.race_key NOT LIKE '%#CAND'
              AND {_SETTLED_COND}
            GROUP BY ph.rank
        """),
        params,
    )).mappings().all()

    by_rank: dict[str, dict] = {}
    for r in rank_rows:
        key = _RANK_KEY_MAP.get(str(r["rank"]), str(r["rank"]))
        by_rank[key] = _make_period_dict(
            int(r["n_picks"] or 0),
            int(r["n_hits"] or 0),
            int(r["total_bet"] or 0),
            int(r["total_payout"] or 0),
        )

    # ランク別候補数 = 見送り含む全行の distinct レース数
    # （write_candidates_wt が候補時点で #7S1/#7U/#7M 行を書き込む）
    paper_cand_rows = (await db.execute(
        text(f"""
            SELECT ph.rank AS rank,
                   COUNT(DISTINCT SPLIT_PART(ph.race_key, '#', 1)) AS n_candidates
            FROM keirin.picks_history ph
            JOIN keirin.wt_races wr
              ON SPLIT_PART(ph.race_key, '#', 1) = wr.race_key
            WHERE {where}
              AND ph.route = 'wt'
              AND ph.rank IN ('SEVEN_S1', '7PLUS_U', '7PLUS_M')
              AND {_SETTLED_COND}
            GROUP BY ph.rank
        """),
        params,
    )).mappings().all()
    for r in paper_cand_rows:
        key = _RANK_KEY_MAP.get(str(r["rank"]), str(r["rank"]))
        n_cand = int(r["n_candidates"] or 0)
        if key not in by_rank and n_cand > 0:
            by_rank[key] = _make_period_dict(0, 0, 0, 0)
        if key in by_rank:
            by_rank[key]["n_candidates"] = n_cand
    result["by_rank"] = by_rank
    return result


async def _get_model_eval(db: AsyncSession, period_type: str = "HOLD") -> dict:
    """keirin.model_evaluation から最新のバックテスト結果を返す。
    ランク別行（model_name に '#7U'/'#7M' サフィックス付き）も by_rank に含める。
    """
    row = (await db.execute(
        text("""
            SELECT n_picks, n_hits, total_bet, total_payout, roi,
                   period_from, period_to
            FROM keirin.model_evaluation
            WHERE period_type = :pt
              AND model_name NOT LIKE '%#7%'
              AND model_name NOT LIKE '%#6S1'
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
    # S/S+（#7ST/#7STP）2026-07-15 全廃・S1（#6S1）/A（#7A）2026-07-17 全廃 —
    # 残存する過去評価行は表示しない（save_model_eval 再実行で行自体も削除される）
    rank_rows = (await db.execute(
        text("""
            SELECT model_name, n_picks, n_hits, total_bet, total_payout, roi
            FROM keirin.model_evaluation
            WHERE period_type = :pt
              AND model_name LIKE '%#7%'
              AND model_name NOT LIKE '%#7ST%'
              AND model_name NOT LIKE '%#7STP%'
              AND model_name NOT LIKE '%#7A'
              AND model_name NOT LIKE '%#7R'
            ORDER BY evaluated_at DESC
        """),
        {"pt": period_type},
    )).mappings().all()

    # 同一 evaluated_at の最新セットのみ使用（ランクキーに重複があれば最新を優先）
    by_rank: dict[str, dict] = {}
    for r in rank_rows:
        suffix = str(r["model_name"]).rsplit("#", 1)[-1]  # "7U" / "7M"
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
        AND ph.rank = '7PLUS_R'
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
