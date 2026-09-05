"""JRA 推奨ページ「平八」ピック一覧のデータ構築。

推奨タブの中身を、レース信頼度一覧（`jra_race_confidence`）から
**平八バッジが付いた馬の一覧**へ置き換えるためのサービス。

平八バッジの条件・実測値は `indices/dm_signals.py` の SIGNAL_HEIHACHI 節を参照。
しきい値は同モジュールの定数を import して使い、二重定義しない。

しきい値は推奨ページのスライダーでユーザーが動かせるため、**絞り込みはフロント側で行う**。
本サービスは「候補（指数順位が `CANDIDATE_INDEX_RANK_MAX` 以内の全馬）」と
既定しきい値を返すだけで、どれをバッジ対象にするかは決めない。回収率の集計も
フロント側（`lib/heihachi.ts` の条件）で行う ── 一覧・回収率・レース詳細のバッジが
必ず同じ判定を通るようにするため、判定ロジックを2箇所に置かない。

既定値の単一真実源は `indices/dm_signals.py` の HEIHACHI_* 定数で、ここから
そのまま配信する。長期の期待値は `HEIHACHI_REFERENCE_*`（バックテスト実測）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession

from ..indices.dm_signals import (
    HEIHACHI_COMP_RANK_MAX,
    HEIHACHI_GRADES,
    HEIHACHI_MAX_ODDS,
    HEIHACHI_MIN_ODDS,
    HEIHACHI_MIN_PLACE_PROB,
)

# バックテスト実測（[[jra_heihachi_badge]] 2023-01〜2026-09, n=1,017）。
# 画面に「長期の目安」として出すための参考値で、当日実績とは別物。
HEIHACHI_REFERENCE_N = 1017
HEIHACHI_REFERENCE_PLACE_RATE = 0.346
HEIHACHI_REFERENCE_WIN_ROI = 1.171
HEIHACHI_REFERENCE_PLACE_ROI = 1.147

# 指数は (race_id, horse_id) ごとに最新版を1行だけ取る（v27/v28 が混在するため）。
# 単勝オッズは jra_race_confidence と同じく odds_history の最新を正とする
# （確定後は race_results.win_odds を優先）。
_ENTRIES_SQL = _text("""
    SELECT
        r.id            AS race_id,
        r.course_name,
        r.race_number,
        r.race_name,
        r.post_time,
        r.grade,
        re.horse_number,
        h.name          AS horse_name,
        ci.composite_index,
        ci.place_probability,
        COALESCE(rr.win_odds, oh.odds) AS win_odds,
        rr.finish_position,
        rr.place_odds   AS result_place_odds,
        rr.win_odds     AS result_win_odds,
        rr.abnormality_code
    FROM keiba.races r
    JOIN keiba.race_entries re ON re.race_id = r.id
    JOIN keiba.horses h        ON h.id = re.horse_id
    LEFT JOIN LATERAL (
        SELECT composite_index, place_probability
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


# フロントのスライダー可動域（lib/heihachi.ts HEIHACHI_RANGES.maxIndexRank.max）に
# 合わせた候補の絞り込み幅。ここを広げるときはフロント側も一緒に広げること。
CANDIDATE_INDEX_RANK_MAX = 5


def select_candidates(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1レース分の出走馬から、しきい値判定の候補になりうる馬を抜き出す。

    DB アクセスなしの純粋関数（テスト対象はここ）。実際にバッジを付けるかは
    フロント側 `matchesHeihachi()` が決めるので、ここでは
    **指数順位が CANDIDATE_INDEX_RANK_MAX 以内** かどうかだけで絞る。
    取消・除外馬（abnormality_code 1/2）は母集団から外すため、後続の順位は繰り上がる。

    Args:
        entries: 同一レースの出走馬 dict のリスト（_ENTRIES_SQL の行）。

    Returns:
        候補馬の dict リスト（index_rank 昇順）。
    """
    if not entries:
        return []
    live = [e for e in entries if e.get("abnormality_code") not in (1, 2)]
    ranked = [e for e in live if e.get("composite_index") is not None]
    ranked.sort(key=lambda e: float(e["composite_index"]), reverse=True)
    return [
        {**e, "index_rank": rank}
        for rank, e in enumerate(ranked[:CANDIDATE_INDEX_RANK_MAX], start=1)
    ]


async def build_heihachi_picks(db: AsyncSession, date: str) -> dict[str, Any]:
    """指定日の平八バッジ候補と、既定しきい値・長期実測を返す。

    絞り込みと回収率集計はフロント側が行う（モジュール docstring 参照）。
    """
    rows = (await db.execute(_ENTRIES_SQL, {"date": date})).mappings().all()
    by_race: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    for row in rows:
        d = dict(row)
        rid = d["race_id"]
        if rid not in by_race:
            by_race[rid] = []
            order.append(rid)
        by_race[rid].append(d)

    candidates: list[dict[str, Any]] = []
    for rid in order:
        for p in select_candidates(by_race[rid]):
            candidates.append({
                "race_id": p["race_id"],
                "course_name": p["course_name"],
                "race_number": p["race_number"],
                "race_name": p["race_name"],
                "post_time": p["post_time"],
                "grade": p["grade"],
                "horse_number": p["horse_number"],
                "horse_name": p["horse_name"],
                "index_rank": p["index_rank"],
                "composite_index": (
                    float(p["composite_index"]) if p["composite_index"] is not None else None
                ),
                "place_probability": (
                    float(p["place_probability"]) if p["place_probability"] is not None else None
                ),
                "win_odds": float(p["win_odds"]) if p["win_odds"] is not None else None,
                "finish_position": p["finish_position"],
                "result_win_odds": (
                    float(p["result_win_odds"]) if p["result_win_odds"] is not None else None
                ),
                "result_place_odds": (
                    float(p["result_place_odds"]) if p["result_place_odds"] is not None else None
                ),
            })
    return {
        "date": date,
        "candidates": candidates,
        "defaults": {
            "max_index_rank": HEIHACHI_COMP_RANK_MAX,
            "min_odds": HEIHACHI_MIN_ODDS,
            "max_odds": HEIHACHI_MAX_ODDS,
            "min_place_prob": HEIHACHI_MIN_PLACE_PROB,
            "graded_only": True,
            "grades": sorted(HEIHACHI_GRADES),
        },
        "reference": {
            "n": HEIHACHI_REFERENCE_N,
            "place_rate": HEIHACHI_REFERENCE_PLACE_RATE,
            "win_roi": HEIHACHI_REFERENCE_WIN_ROI,
            "place_roi": HEIHACHI_REFERENCE_PLACE_ROI,
        },
    }
