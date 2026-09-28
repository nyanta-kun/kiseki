"""地方「激走 / 見送り」判定の入力を DB から組み立てる。

判定そのものは `indices/chihou_gekisou.judge_gekisou`（純関数）が正本。
ここがやるのは「どの時点の指数とオッズで判定するか」を決めることだけ。

🔴 **前向き記録（`chihou.place_picks`）があるレースは、その記録が唯一の正本。**
記録は発走 5〜6 分前に全出走馬のモデル複勝確率と発走前単勝オッズを凍結している。
再計算はオッズを引き直すので、終わったレースを開くと「その日に出ていた印」と
食い違う（注目馬で 2026-08-26 に実際に起きた・`resolve_place_picks` の経緯）。
記録が無いレース（まだスナップショット前・記録開始 2026-08-14 より前）だけ、
その時点の最新オッズと現行指数で判定する（= 「候補」扱い）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import (
    ChihouCalculatedIndex,
    ChihouPlacePick,
    ChihouPlacePickRace,
    ChihouRaceEntry,
)
from ..indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION
from ..indices.chihou_gekisou import GekisouVerdict, judge_gekisou
from .chihou_odds_query import latest_odds_sql

SOURCE_SNAPSHOT: str = "snapshot"  # 発走前スナップショットで確定
SOURCE_LIVE: str = "live"  # 最新オッズでの暫定（発走前はまだ動く）


@dataclass(frozen=True)
class SourcedVerdict:
    """判定結果と、それをどの入力で出したか。

    Attributes:
        verdict: 判定結果
        source: "snapshot"（記録で確定）/ "live"（最新オッズでの暫定）
        win_odds: 判定に使った単勝オッズ（馬番 → 倍率）
        place_odds: 判定時点の複勝オッズ下限（馬番 → 倍率。記録側のみ）
    """

    verdict: GekisouVerdict
    source: str
    win_odds: dict[int, float]
    place_odds: dict[int, float]


async def fetch_gekisou_verdicts(db: AsyncSession, race_ids: list[int]) -> dict[int, SourcedVerdict]:
    """レースごとの激走 / 見送り判定を返す。

    Args:
        db: セッション
        race_ids: 対象レース

    Returns:
        race_id → 判定。指数もオッズも無いレースは含まれない。
    """
    if not race_ids:
        return {}
    out: dict[int, SourcedVerdict] = {}

    # --- 前向き記録のあるレース（正本） ---
    logged_rows = await db.execute(
        select(ChihouPlacePickRace.id, ChihouPlacePickRace.race_id).where(ChihouPlacePickRace.race_id.in_(race_ids))
    )
    logged = {int(pid): int(rid) for pid, rid in logged_rows.all()}
    if logged:
        pick_rows = await db.execute(
            select(
                ChihouPlacePick.pick_race_id,
                ChihouPlacePick.horse_number,
                ChihouPlacePick.place_probability,
                ChihouPlacePick.pre_win_odds,
                ChihouPlacePick.pre_place_odds,
            ).where(ChihouPlacePick.pick_race_id.in_(list(logged)))
        )
        by_race: dict[int, list[tuple]] = defaultdict(list)
        for pid, hn, pp, wo, po in pick_rows.all():
            if hn is not None:
                by_race[logged[int(pid)]].append((int(hn), pp, wo, po))
        for rid, rows in by_race.items():
            out[rid] = SourcedVerdict(
                verdict=judge_gekisou({hn: (pp, wo) for hn, pp, wo, _po in rows}),
                source=SOURCE_SNAPSHOT,
                win_odds={hn: float(wo) for hn, _pp, wo, _po in rows if wo is not None},
                place_odds={hn: float(po) for hn, _pp, _wo, po in rows if po is not None},
            )

    # --- 記録の無いレース（最新オッズでの暫定） ---
    live_ids = [rid for rid in race_ids if rid not in out]
    if not live_ids:
        return out

    prob_rows = await db.execute(
        select(
            ChihouCalculatedIndex.race_id,
            ChihouRaceEntry.horse_number,
            ChihouCalculatedIndex.place_probability,
        )
        .join(
            ChihouRaceEntry,
            (ChihouRaceEntry.race_id == ChihouCalculatedIndex.race_id)
            & (ChihouRaceEntry.horse_id == ChihouCalculatedIndex.horse_id),
        )
        .where(ChihouCalculatedIndex.race_id.in_(live_ids))
        .where(ChihouCalculatedIndex.version == CHIHOU_COMPOSITE_VERSION)
    )
    probs: dict[int, dict[int, float | None]] = defaultdict(dict)
    for rid, hn, pp in prob_rows.all():
        if hn is not None:
            probs[int(rid)][int(hn)] = float(pp) if pp is not None else None

    # 発走時刻以前の最新オッズ（画面・前向き記録と同じクエリ）
    odds_rows = await db.execute(sql_text(latest_odds_sql(["win", "place"])), {"race_ids": live_ids})
    win: dict[int, dict[int, float]] = defaultdict(dict)
    place: dict[int, dict[int, float]] = defaultdict(dict)
    for rid, bet_type, combo, odds_val in odds_rows.all():
        if odds_val is None or not str(combo).isdigit():
            continue
        target = win if bet_type == "win" else place
        target[int(rid)][int(combo)] = float(odds_val)

    for rid in live_ids:
        if rid not in probs or rid not in win:
            continue
        # 指数が無い馬も None として渡す（オッズがあるのに指数が欠けていれば判定しない）
        runners = {hn: (probs[rid].get(hn), wo) for hn, wo in win[rid].items()}
        out[rid] = SourcedVerdict(
            verdict=judge_gekisou(runners),
            source=SOURCE_LIVE,
            win_odds=dict(win[rid]),
            place_odds=dict(place.get(rid, {})),
        )
    return out
