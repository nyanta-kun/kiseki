"""JRA レース信頼度一覧（推奨ページ）のデータ構築。

「その日の全レースを信頼度順に並べ、各レースの**市場1番人気馬**を1行で見せる」ための
サービス。従来の推奨カード（`build_hit_tier_recommendations`）は tier が C のレースを
落として1レース1推奨を出すものだったが、本モジュールは**落とさず全レースを返す**。
並び替えはフロント側で行うため、ここでは常に発走時刻順で返す。

信頼度は既存の `indices.confidence` をそのまま使う（tier / confidence_score の定義を
推奨カードと揃えるため）。したがって tier の判定基準は従来どおり **指数1位馬**（
composite_index 最上位）が市場1番人気と一致するか（market_agree）であり、
**表示する馬は市場1番人気**である点に注意。両者が一致するのは全レースの約56%。

⚠️ win_probability は 2026-08-22 に較正ヘッドを再学習している。過去に遡って
この値を検証する場合は `jra_protocol.TEST_START` 以降に限ること
（memory: jra_iswin_full_refit_leak_2026_08_22）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession

from ..indices.confidence import (
    JRA_GAP_FULL_SCORE,
    calculate_market_chaos,
    calculate_race_confidence,
    calculate_recommend_rank,
    is_market_favorite,
)

# 1レース = 1行に畳む前の、出走馬単位の取得 SQL。
# - 指数は (race_id, horse_id) ごとに最新版を1行だけ取る（v26/v27 が混在するため）
# - 単勝オッズは keiba.odds_history の最新 fetched_at を LATERAL で1件だけ引く。
#   `keiba.latest_odds`（上書き方式・地方で使用中）の方が軽いが、JRA 側の既存
#   エンドポイント（races.py の top-probability）が odds_history を正としているため
#   同じ経路に揃える。両者の値は実測で一致する。
_ENTRIES_SQL = _text("""
    SELECT
        r.id            AS race_id,
        r.course_name,
        r.race_number,
        r.race_name,
        r.post_time,
        r.surface,
        r.distance,
        r.head_count,
        re.horse_number,
        h.name          AS horse_name,
        ci.composite_index,
        ci.win_probability,
        COALESCE(rr.win_odds, oh.odds) AS win_odds,
        rr.finish_position
    FROM keiba.races r
    JOIN keiba.race_entries re ON re.race_id = r.id
    JOIN keiba.horses h        ON h.id = re.horse_id
    LEFT JOIN LATERAL (
        SELECT composite_index, win_probability
        FROM keiba.calculated_indices
        WHERE race_id = r.id AND horse_id = re.horse_id
        ORDER BY version DESC, calculated_at DESC
        LIMIT 1
    ) ci ON TRUE
    LEFT JOIN LATERAL (
        SELECT odds FROM keiba.odds_history
        WHERE race_id = r.id
          AND bet_type = 'win'
          AND combination = re.horse_number::text
        ORDER BY fetched_at DESC
        LIMIT 1
    ) oh ON TRUE
    LEFT JOIN keiba.race_results rr
        ON rr.race_id = r.id AND rr.horse_id = re.horse_id
    WHERE r.date = :date
    ORDER BY r.post_time ASC NULLS LAST, r.race_number ASC, re.horse_number ASC
""")


def summarize_race(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """1レース分の出走馬リストを、一覧の1行に畳む。

    DB アクセスなしの純粋関数（テスト対象はここ）。

    Args:
        entries: 同一レースの出走馬 dict のリスト。各 dict は
            `composite_index` / `win_probability` / `win_odds` /
            `horse_number` / `horse_name` と、レース属性を持つ。

    Returns:
        一覧1行分の dict。指数やオッズが欠けている場合、該当項目は None になるが
        **行自体は必ず返す**（全レースを表示するため）。
    """
    head = entries[0]
    n_entries = len(entries)

    indices = [e["composite_index"] for e in entries if e.get("composite_index") is not None]
    probs_by_horse = {e["horse_number"]: e.get("win_probability") for e in entries}
    odds_list = [e["win_odds"] for e in entries if e.get("win_odds") is not None]

    # head_count は確定成績から埋まる列で、発走前は NULL。出走頭数で代替する
    # （composite._compute_composite と同じフォールバック）
    head_count = head.get("head_count") or n_entries

    # --- 信頼度スコア（既存 confidence.py。JRA は gap 較正値が専用） ---
    confidence: dict[str, Any] | None = None
    if indices:
        # calculate_race_confidence は win_probabilities を sorted() するため、
        # None が1つでも混ざると TypeError になる。全頭そろっている時だけ渡す。
        raw_probs = [
            e.get("win_probability") for e in entries if e.get("composite_index") is not None
        ]
        kept = [float(p) for p in raw_probs if p is not None]
        probs: list[float] | None = kept if len(kept) == len(raw_probs) and kept else None
        confidence = calculate_race_confidence(
            indices,
            head_count=head_count,
            win_probabilities=probs,
            gap_full_score=JRA_GAP_FULL_SCORE,
        )

    # --- tier 判定（従来どおり「指数1位馬」基準） ---
    tier: str | None = None
    if confidence is not None:
        top_by_index = max(
            (e for e in entries if e.get("composite_index") is not None),
            key=lambda e: e["composite_index"],
        )
        win_odds_top = top_by_index.get("win_odds")
        tier = calculate_recommend_rank(
            confidence_score=confidence["score"],
            win_odds_top=win_odds_top,
            market_agree=is_market_favorite(win_odds_top, odds_list or None),
            entropy_norm=calculate_market_chaos(odds_list)["entropy_norm"],
        )

    # --- 表示する馬 = 市場1番人気（単勝オッズ最小） ---
    fav = min(
        (e for e in entries if e.get("win_odds") is not None),
        key=lambda e: e["win_odds"],
        default=None,
    )
    fav_prob = probs_by_horse.get(fav["horse_number"]) if fav else None
    fav_odds = fav["win_odds"] if fav else None
    # 単勝EV = 単勝オッズ × 単勝率。丸めはフロント側（表示は小数第1位）に任せ、
    # ここでは並び替えが潰れないよう素の値を返す。
    ev = fav_odds * fav_prob if (fav_odds is not None and fav_prob is not None) else None

    return {
        "race_id": head["race_id"],
        "course_name": head["course_name"],
        "race_number": head["race_number"],
        "race_name": head["race_name"],
        "post_time": head["post_time"],
        "surface": head["surface"],
        "distance": head["distance"],
        "head_count": head_count,
        "confidence_score": confidence["score"] if confidence else None,
        "tier": tier,
        "horse_number": fav["horse_number"] if fav else None,
        "horse_name": fav["horse_name"] if fav else None,
        "win_odds": fav_odds,
        "win_probability": fav_prob,
        "ev": ev,
        # 一覧では列にしていないが、確定後に着順バッジを足したくなったとき用に返す
        "finish_position": fav.get("finish_position") if fav else None,
    }


async def build_race_confidence_list(db: AsyncSession, date: str) -> list[dict[str, Any]]:
    """指定日の全レースを、信頼度と市場1番人気馬の情報つきで返す。

    返却順は発走時刻順（フロントで任意列に並び替える）。
    指数・オッズが未取得のレースも欠損のまま返す。
    """
    result = await db.execute(_ENTRIES_SQL, {"date": date})
    rows = result.mappings().all()

    grouped: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    for row in rows:
        e = dict(row)
        # Numeric → float（Decimal のまま JSON に載せない）
        for k in ("composite_index", "win_probability", "win_odds"):
            if e.get(k) is not None:
                e[k] = float(e[k])
        rid = e["race_id"]
        if rid not in grouped:
            grouped[rid] = []
            order.append(rid)
        grouped[rid].append(e)

    return [summarize_race(grouped[rid]) for rid in order]
