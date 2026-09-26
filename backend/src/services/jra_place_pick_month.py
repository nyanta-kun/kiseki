"""推奨ページ「複勝ピック」の当月一覧（結果つき）。

判定は `jra_place_pick.select_place_pick()` に一本化し、入力は**前向き記録**
（`keiba.hit_tier_races` / `keiba.hit_tier_picks`・発走約10分前のスナップショット）
から取る。

🔴 **現在の `calculated_indices` と最新オッズから作り直してはいけない。**
指数は馬体重到着やバックフィルで上書きされ、オッズは締切まで動く。
あとから作り直すと「発走前に画面に出ていたピック」とずれ、監視にならない。
スナップショットは撮った後に変わらないので、一覧は何度引いても同じになる。

取消・除外（`abnormality_code` 1/2）の馬はフィールドから外して判定する
（探索時の集計と揃えるため）。ピックした馬自身が取消・除外なら「返還」として
集計から外す。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import HitTierPick, HitTierRace, Race
from .jra_place_pick import (
    RULE_VERSION,
    PlacePickHorse,
    place_probability_ranks,
    place_slots,
    select_place_pick,
)

SCRATCH_CODES = (1, 2)
STAKE = 100


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _status(pick: HitTierPick, slots: int) -> str:
    """pending（未確定）/ void（返還）/ hit / miss。"""
    if pick.abnormality_code in SCRATCH_CODES:
        return "void"
    fp = pick.finish_position
    if fp is None or fp <= 0:
        return "pending" if pick.settled_at is None else "miss"
    if fp <= slots and pick.place_payout_odds is not None:
        return "hit"
    return "miss"


def month_races_stmt(month: str) -> Select[Any]:
    """当月のスナップショット（レース行 + レース情報）を引く select。同期・非同期で共用。"""
    return (
        select(HitTierRace, Race.race_name, Race.surface, Race.distance)
        .join(Race, Race.id == HitTierRace.race_id)
        .where(HitTierRace.date.between(f"{month}01", f"{month}31"))
    )


async def build_place_pick_month(db: AsyncSession, month: str) -> dict[str, Any]:
    """`month`（YYYYMM）の複勝ピック一覧と集計を返す。"""
    log_races = (await db.execute(month_races_stmt(month))).all()
    race_ids = [lr.race_id for lr, *_ in log_races]
    picks: list[HitTierPick] = []
    if race_ids:
        picks = list((await db.execute(select(HitTierPick).where(HitTierPick.race_id.in_(race_ids)))).scalars().all())
    return assemble_place_pick_month(month, log_races, picks)


def assemble_place_pick_month(month: str, log_races: Sequence[Any], picks: Iterable[HitTierPick]) -> dict[str, Any]:
    """読み出した行から一覧と集計を組み立てる（DB に触らない）。"""
    picks_by_race: dict[int, list[HitTierPick]] = defaultdict(list)
    for p in picks:
        picks_by_race[p.race_id].append(p)

    rows: list[dict[str, Any]] = []
    for lr, race_name, surface, distance in log_races:
        horses = [p for p in picks_by_race.get(lr.race_id, []) if p.abnormality_code not in SCRATCH_CODES]
        inputs = [
            PlacePickHorse(
                horse_number=p.horse_number,
                win_odds=p.pre_win_odds,
                place_odds=p.pre_place_odds,
                place_probability=_f(p.place_probability),
            )
            for p in horses
        ]
        hn = select_place_pick(inputs)
        if hn is None:
            continue
        pick = next(p for p in horses if p.horse_number == hn)
        field_size = sum(1 for i in inputs if i.win_odds is not None and i.win_odds > 0)
        status = _status(pick, place_slots(field_size))
        payout = round(pick.place_payout_odds * STAKE) if status == "hit" and pick.place_payout_odds is not None else 0
        rows.append(
            {
                "date": lr.date,
                "race_id": lr.race_id,
                "course_name": lr.course_name,
                "race_number": lr.race_number,
                "race_name": race_name,
                "post_time": lr.post_time,
                "surface": surface,
                "distance": distance,
                "field_size": field_size,
                "horse_number": hn,
                "horse_name": pick.horse_name,
                "pre_win_odds": pick.pre_win_odds,
                "pre_place_odds": pick.pre_place_odds,
                "place_probability": _f(pick.place_probability),
                "place_probability_rank": place_probability_ranks(inputs).get(hn),
                "pop_rank": pick.pop_rank,
                "finish_position": pick.finish_position,
                "place_payout": payout if status == "hit" else None,
                "status": status,
            }
        )

    rows.sort(key=lambda r: (r["date"], r["post_time"] or "", r["race_number"] or 0), reverse=True)
    settled = [r for r in rows if r["status"] in ("hit", "miss")]
    n_hits = sum(1 for r in settled if r["status"] == "hit")
    invest = len(settled) * STAKE
    ret = sum(r["place_payout"] or 0 for r in settled)
    return {
        "month": month,
        "rule_version": RULE_VERSION,
        "n_races_logged": len(log_races),
        "summary": {
            "n_picks": len(rows),
            "n_settled": len(settled),
            "n_hits": n_hits,
            "hit_rate": (n_hits / len(settled)) if settled else None,
            "invest": invest,
            "payout": ret,
            "roi": (ret / invest) if invest else None,
        },
        "picks": rows,
    }
