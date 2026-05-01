"""v24 サブ指数を流用して v25 を高速生成する。

v24 と v25 で変わったのは pace_handicap_index と合成式（線形和→乗数式）のみ。
他 17 サブ指数は同一のため、再計算せず DB から読み出して再利用する。

通常のバックフィル比 約10倍速度。

使い方:
    python scripts/recompute_v25_from_v24.py --start 20230501 --end 20260501
    python scripts/recompute_v25_from_v24.py --start 20230501 --end 20240130 --skip-existing
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import select, text

from src.db.models import CalculatedIndex, Race, RaceEntry
from src.db.session import AsyncSessionLocal
from src.indices.composite import COMPOSITE_VERSION, CompositeIndexCalculator
from src.indices.pace_handicap import PaceHandicapCalculator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("recompute_v25")

V24_VERSION = 24

_DATE_QUERY = text(
    """
    SELECT DISTINCT r.date
    FROM keiba.races r
    WHERE r.date BETWEEN :start AND :end
      AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
    ORDER BY r.date DESC
    """
)


def _to_dec(v: float | None) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


def _f(v: Decimal | None, default: float = 50.0) -> float:
    """Decimal | None → float、None なら default"""
    return default if v is None else float(v)


async def _process_race(race_id: int, race_date: str) -> int:
    """1レースを v24 サブ指数から v25 へ再合成して保存する。

    Returns:
        保存した馬数（既に v25 が存在した場合は 0）
    """
    async with AsyncSessionLocal() as session:
        # v24 サブ指数を全馬分まとめて読み込み
        v24_rows = (
            await session.execute(
                select(CalculatedIndex).where(
                    CalculatedIndex.race_id == race_id,
                    CalculatedIndex.version == V24_VERSION,
                )
            )
        ).scalars().all()
        if not v24_rows:
            return 0

        # 既存 v25 を確認（skip-existing 用）
        v25_existing = (
            await session.execute(
                select(CalculatedIndex).where(
                    CalculatedIndex.race_id == race_id,
                    CalculatedIndex.version == COMPOSITE_VERSION,
                )
            )
        ).scalars().all()
        v25_map: dict[int, CalculatedIndex] = {r.horse_id: r for r in v25_existing}

        # pace_handicap だけ再計算（騎手戦法統合 v25 版）
        ph_calc = PaceHandicapCalculator(session)
        ph_map = await ph_calc.calculate_batch(race_id)

        # entries から jvan_dm を取得
        entries_rows = (
            await session.execute(
                select(RaceEntry).where(RaceEntry.race_id == race_id)
            )
        ).scalars().all()
        jvan_time_map: dict[int, float] = {}
        jvan_battle_map: dict[int, float] = {}
        for e in entries_rows:
            jvan_time_map[e.horse_id] = (
                float(e.jvan_time_dm) if e.jvan_time_dm is not None else 50.0
            )
            jvan_battle_map[e.horse_id] = (
                float(e.jvan_battle_dm) if e.jvan_battle_dm is not None else 50.0
            )

        # レース情報（segment weights 用）
        race = (
            await session.execute(select(Race).where(Race.id == race_id))
        ).scalar_one_or_none()
        if not race:
            return 0

        # CompositeIndexCalculator は呼ばず、内部で同等計算（v25 乗数式）
        calc = CompositeIndexCalculator(session)
        # レース内平均で None 補完するために pace_handicap_map の None を平均で埋める
        from src.indices.composite import _fill_with_race_mean
        ph_filled = _fill_with_race_mean(ph_map)

        results: list[dict] = []
        for v24 in v24_rows:
            hid = v24.horse_id
            row = calc._compute_composite(
                horse_id=hid,
                speed=_f(v24.speed_index),
                last3f=_f(v24.last_3f_index),
                course_aptitude=_f(v24.course_aptitude),
                position_advantage=_f(v24.position_advantage),
                rotation=_f(v24.rotation_index),
                jockey=_f(v24.jockey_index),
                pace=_f(v24.pace_index),
                pace_handicap=ph_filled.get(hid, 50.0),
                pedigree=_f(v24.pedigree_index),
                training=_f(v24.training_index),
                anagusa=_f(v24.anagusa_index),
                paddock=_f(v24.paddock_index),
                rebound=_f(v24.rebound_index),
                rivals_growth=_f(v24.rivals_growth_index),
                career_phase=_f(v24.career_phase_index),
                distance_change=_f(v24.distance_change_index),
                jockey_trainer_combo=_f(v24.jockey_trainer_combo_index),
                going_pedigree=_f(v24.going_pedigree_index),
                jvan_time_dm=jvan_time_map.get(hid, 50.0),
                jvan_battle_dm=jvan_battle_map.get(hid, 50.0),
                weights=None,
            )
            results.append({"horse_id": hid, **row})

        # 確率計算
        CompositeIndexCalculator._attach_probabilities(results)

        # v25 を upsert
        new_records: list[CalculatedIndex] = []
        for r in results:
            hid = r["horse_id"]
            v24 = next((x for x in v24_rows if x.horse_id == hid), None)
            kwargs = {
                # サブ指数は v24 と同じ値を保存（再現性のため）
                "speed_index": v24.speed_index if v24 else None,
                "last_3f_index": v24.last_3f_index if v24 else None,
                "course_aptitude": v24.course_aptitude if v24 else None,
                "position_advantage": v24.position_advantage if v24 else None,
                "rotation_index": v24.rotation_index if v24 else None,
                "jockey_index": v24.jockey_index if v24 else None,
                "pace_index": v24.pace_index if v24 else None,
                "pedigree_index": v24.pedigree_index if v24 else None,
                "training_index": v24.training_index if v24 else None,
                "anagusa_index": v24.anagusa_index if v24 else None,
                "paddock_index": v24.paddock_index if v24 else None,
                "rebound_index": v24.rebound_index if v24 else None,
                "rivals_growth_index": v24.rivals_growth_index if v24 else None,
                "career_phase_index": v24.career_phase_index if v24 else None,
                "distance_change_index": v24.distance_change_index if v24 else None,
                "jockey_trainer_combo_index": v24.jockey_trainer_combo_index if v24 else None,
                "going_pedigree_index": v24.going_pedigree_index if v24 else None,
                # v25 で変わるもの
                "composite_index": _to_dec(r.get("composite_index")),
                "win_probability": _to_dec(r.get("win_probability")),
                "place_probability": _to_dec(r.get("place_probability")),
            }
            if hid in v25_map:
                exist = v25_map[hid]
                for k, v in kwargs.items():
                    setattr(exist, k, v)
                exist.calculated_at = datetime.now()
            else:
                new_records.append(
                    CalculatedIndex(
                        race_id=race_id,
                        horse_id=hid,
                        version=COMPOSITE_VERSION,
                        **kwargs,
                    )
                )
        if new_records:
            session.add_all(new_records)
        await session.commit()
        return len(results)


async def _process_date(date: str, sem: asyncio.Semaphore, skip_existing: bool) -> int:
    """指定日のレース全件を v25 化する。"""
    async with AsyncSessionLocal() as session:
        rids = (
            await session.execute(
                text(
                    """
                    SELECT id FROM keiba.races
                    WHERE date = :date
                      AND course IN ('01','02','03','04','05','06','07','08','09','10')
                    """
                ),
                {"date": date},
            )
        ).all()
        race_ids = [r.id for r in rids]

    async def _one(rid: int) -> int:
        async with sem:
            try:
                return await _process_race(rid, date)
            except Exception as e:  # noqa: BLE001
                logger.error(f"race_id={rid} ({date}) error: {e}")
                return 0

    counts = await asyncio.gather(*[_one(r) for r in race_ids])
    return sum(counts)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--skip-existing", action="store_true")
    p.add_argument("--concurrency", type=int, default=8, help="並列レース数")
    args = p.parse_args()

    logger.info(
        f"v24 → v{COMPOSITE_VERSION} 再合成: {args.start}〜{args.end} "
        f"並列={args.concurrency}"
    )

    async with AsyncSessionLocal() as session:
        date_rows = (await session.execute(_DATE_QUERY, {"start": args.start, "end": args.end})).all()
    dates = [r.date for r in date_rows]
    logger.info(f"対象開催日: {len(dates)}日")

    sem = asyncio.Semaphore(args.concurrency)
    total = 0
    for i, d in enumerate(dates, 1):
        n = await _process_date(d, sem, args.skip_existing)
        total += n
        if i % 5 == 0 or i == len(dates):
            logger.info(f"[{i:3d}/{len(dates)}] {d}: {n:4d}頭 累計 {total:,}頭")
    logger.info(f"完了: {len(dates)}日 / {total:,}頭")


if __name__ == "__main__":
    asyncio.run(main())
