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

## 当日分は「候補 → 確定」の2段

- **候補**（`stage="candidate"`）: 当日のまだスナップショットが無く、発走前のレース。
  最新オッズと現在の指数で判定するので、オッズ次第で出たり消えたりする。
  オッズはスナップショットと同じ `_latest_win_place_odds` で取る
- **確定**（`stage="confirmed"`）: 発走約10分前のスナップショットで判定したもの。
  以後は変わらない。**集計（的中率・回収率）は確定分だけ**で行う
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CalculatedIndex,
    EntryChange,
    HitTierPick,
    HitTierRace,
    Horse,
    Race,
    RaceEntry,
    RaceResult,
)
from ..indices.composite import COMPOSITE_VERSION
from .jra_hit_tier_log import JRA_COURSE_CODES, JST, _latest_win_place_odds, parse_post_datetime
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


async def build_place_pick_month(db: AsyncSession, month: str, *, now: datetime | None = None) -> dict[str, Any]:
    """`month`（YYYYMM）の複勝ピック一覧と集計を返す。当日分は候補も含む。"""
    log_races = (await db.execute(month_races_stmt(month))).all()
    race_ids = [lr.race_id for lr, *_ in log_races]
    picks: list[HitTierPick] = []
    if race_ids:
        picks = list((await db.execute(select(HitTierPick).where(HitTierPick.race_id.in_(race_ids)))).scalars().all())
    result = assemble_place_pick_month(month, log_races, picks)
    candidates = await _today_candidates(db, month, now or datetime.now(JST))
    if candidates:
        result["picks"] = _sorted(candidates + result["picks"])
        result["summary"]["n_candidates"] = len(candidates)
    return result


async def _today_candidates(
    db: AsyncSession, month: str, now: datetime, *, skip_snapshotted: bool = True
) -> list[dict[str, Any]]:
    """当日の、スナップショット未取得かつ発走前のレースの候補を返す。

    `skip_snapshotted=False` は検証用（撮影済みのレースも最新オッズで判定し直す）。
    """
    today = now.astimezone(JST).strftime("%Y%m%d")
    if not today.startswith(month):
        return []
    races = (
        (await db.execute(select(Race).where(Race.date == today).where(Race.course.in_(list(JRA_COURSE_CODES)))))
        .scalars()
        .all()
    )
    snapped = (
        set(
            (await db.execute(select(HitTierRace.race_id).where(HitTierRace.race_id.in_([r.id for r in races]))))
            .scalars()
            .all()
        )
        if races
        else set()
    )
    targets = []
    for r in races:
        post_at = parse_post_datetime(r.date, r.post_time)
        if (r.id not in snapped or not skip_snapshotted) and post_at is not None and post_at > now:
            targets.append(r)
    if not targets:
        return []
    ids = [r.id for r in targets]

    idx_rows = (
        await db.execute(
            select(
                CalculatedIndex.race_id,
                RaceEntry.horse_number,
                CalculatedIndex.place_probability,
                Horse.name,
            )
            .join(
                RaceEntry,
                (RaceEntry.race_id == CalculatedIndex.race_id) & (RaceEntry.horse_id == CalculatedIndex.horse_id),
            )
            .join(Horse, Horse.id == CalculatedIndex.horse_id)
            .where(CalculatedIndex.race_id.in_(ids))
            .where(CalculatedIndex.version == COMPOSITE_VERSION)
            .where(RaceEntry.horse_number > 0)
        )
    ).all()
    idx: dict[int, dict[int, tuple[float | None, str | None]]] = defaultdict(dict)
    for rid, hn, pp, name in idx_rows:
        idx[int(rid)][int(hn)] = (_f(pp), name)

    scratched: dict[int, set[int]] = defaultdict(set)
    for rid, hn in (
        await db.execute(
            select(RaceEntry.race_id, RaceEntry.horse_number)
            .join(
                EntryChange,
                (EntryChange.race_id == RaceEntry.race_id) & (EntryChange.horse_id == RaceEntry.horse_id),
            )
            .where(RaceEntry.race_id.in_(ids))
            .where(EntryChange.change_type == "scratch")
        )
    ).all():
        scratched[int(rid)].add(int(hn))
    for rid, hn in (
        await db.execute(
            select(RaceResult.race_id, RaceResult.horse_number)
            .where(RaceResult.race_id.in_(ids))
            .where(RaceResult.abnormality_code.in_(SCRATCH_CODES))
        )
    ).all():
        if hn is not None:
            scratched[int(rid)].add(int(hn))

    rows: list[dict[str, Any]] = []
    for r in targets:
        # レースごとに呼ぶ: まとめて渡すと「全体の最新から5分以内」で絞られ、
        # 取得が少し遅れたレースが黙って落ちる
        win_by, place_by = await _latest_win_place_odds(db, [r.id])
        win, place = win_by.get(r.id, {}), place_by.get(r.id, {})
        horses = {hn: v for hn, v in idx.get(r.id, {}).items() if hn not in scratched[r.id]}
        inputs = [
            PlacePickHorse(
                horse_number=hn,
                win_odds=win.get(hn),
                place_odds=place.get(hn),
                place_probability=pp,
            )
            for hn, (pp, _name) in horses.items()
        ]
        hn = select_place_pick(inputs)
        if hn is None:
            continue
        field = sorted((i for i in inputs if i.win_odds and i.win_odds > 0), key=lambda i: i.win_odds or 0)
        rows.append(
            {
                "date": r.date,
                "race_id": r.id,
                "course_name": r.course_name,
                "race_number": r.race_number,
                "race_name": r.race_name,
                "post_time": r.post_time,
                "surface": r.surface,
                "distance": r.distance,
                "field_size": len(field),
                "horse_number": hn,
                "horse_name": horses[hn][1],
                "pre_win_odds": win.get(hn),
                "pre_place_odds": place.get(hn),
                "place_probability": horses[hn][0],
                "place_probability_rank": place_probability_ranks(inputs).get(hn),
                "pop_rank": next((k for k, i in enumerate(field, 1) if i.horse_number == hn), None),
                "finish_position": None,
                "place_payout": None,
                "stage": "candidate",
                "status": "candidate",
            }
        )
    return rows


def _sorted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """新しい日付が上・同日内は発走の遅い順（直近の発走が上に来る）。"""
    return sorted(rows, key=lambda r: (r["date"], r["post_time"] or "", r["race_number"] or 0), reverse=True)


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
                "stage": "confirmed",
                "status": status,
            }
        )

    rows = _sorted(rows)
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
            "n_candidates": 0,
            "n_settled": len(settled),
            "n_hits": n_hits,
            "hit_rate": (n_hits / len(settled)) if settled else None,
            "invest": invest,
            "payout": ret,
            "roi": (ret / invest) if invest else None,
        },
        "picks": rows,
    }
