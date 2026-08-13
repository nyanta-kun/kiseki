"""地方競馬 注目馬の**前向き記録**（発走前スナップショット → 翌日確定）。

## なぜ要るか

凍結した運用点

    発走前6番人気以下 ∧ 指数5位以内 ∧ 市場上位3頭シェア<0.63 ∧ 8頭以上 → 指数上位2頭まで

は DISCOVERY（2026-04-07〜06-30 / 2,894R）の探索で選ばれたもので、**確認窓が無い**。
HOLDOUT(2026-07) は先行検証で開封済み。したがって汚染されていない確認窓は
「これから起きるレース」しか無い（台帳 `docs/chihou_rebuild_2026_08.md` 11.5〜11.6）。

しかも後付け集計ができない。`chihou.calculated_indices` の現行 version 行は
当日 21:30 JST の再算出で上書きされ、そのとき市場特徴の入力が
`race_results.win_odds`（＝確定オッズ）に変わる（台帳 5.4）。
**日中ユーザーに提示された指数は DB に残らない。**

そこで 2 段構成にする:

1. `snapshot_place_picks()` — 発走 `SNAPSHOT_LEAD_MINUTES` 分前に、そのレースの
   全出走馬の指数・発走前オッズ・判定結果を `chihou.place_pick_races` /
   `chihou.place_picks` へ保存する（毎分 cron）
2. `settle_place_picks()` — 翌日、確定結果を同じ行へ書き戻す（日次 cron）

## 記録の範囲を欲張っている理由

- **推奨が出なかったレースも記録する。** 「出す/出さない」の判断自体が情報を持つ
  （台帳 11.3: 推奨ありのレースは人気薄が複勝圏に来る率 80.4% / 棄権は 51.0%）。
  棄権側が無いと運用点の評価が片肺になる
- **推奨馬だけでなく全出走馬を記録する。** 指数が上書きされる以上、
  「指数2位内にしていたら」（台帳 12.5 の保留仮説）のような別案の事後評価は、
  全馬ぶんの指数がここに残っていなければ二度とできない

⚠️ **判定は本番と同じ関数（`chihou_is_place_pick` / `chihou_select_place_picks`）を
呼ぶこと。** 条件をこのモジュールに書き写すと、本番の閾値を変えたときに記録だけが
古い条件のまま残り、記録の意味が消える。閾値を変えた場合は `rule_version` の値が
変わるので、集計時に混ぜないこと。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import (
    ChihouCalculatedIndex,
    ChihouHorse,
    ChihouPlacePick,
    ChihouPlacePickRace,
    ChihouRace,
    ChihouRaceEntry,
    ChihouRaceResult,
)
from ..indices.buy_signal import (
    CHIHOU_OPEN_RACE_MAX_TOP3_SHARE,
    CHIHOU_PICK_MAX_INDEX_RANK,
    CHIHOU_PICK_MAX_PER_RACE,
    CHIHOU_PICK_MIN_POP_RANK,
    CHIHOU_PLACE_MIN_HEAD_COUNT,
    chihou_effective_head_count,
    chihou_is_place_pick,
    chihou_market_top3_share,
    chihou_popularity_ranks,
    chihou_select_place_picks,
)
from ..indices.chihou_calculator import BANEI_COURSE_CODE, CHIHOU_COMPOSITE_VERSION
from .chihou_odds_query import LATEST_ODDS_SQL

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# 発走の何分前を「その日の実力」として記録するか。
#
# 台帳 10.3 の検証は**発走5分前オッズ**で行った（確定オッズを使うと look-ahead になり
# 条件が崩壊する）。記録もそこへ揃える。毎分 cron で回すので、実際に撮れるのは
# 1〜SNAPSHOT_LEAD_MINUTES 分前のいずれか。
SNAPSHOT_LEAD_MINUTES: int = 6

# 判定ルールの署名。閾値を変えたら自動的に値が変わるので、集計時に世代を混ぜずに済む。
PICK_RULE_VERSION: str = (
    f"pop>={CHIHOU_PICK_MIN_POP_RANK},"
    f"idx<={CHIHOU_PICK_MAX_INDEX_RANK},"
    f"share<{CHIHOU_OPEN_RACE_MAX_TOP3_SHARE},"
    f"head>={CHIHOU_PLACE_MIN_HEAD_COUNT},"
    f"max{CHIHOU_PICK_MAX_PER_RACE}"
)

# 「人気薄」の定義（棄権側の答え合わせ用）。判定と同じ線を使う。
UPSET_POP_RANK: int = CHIHOU_PICK_MIN_POP_RANK


def parse_post_datetime(date: str, post_time: str | None) -> datetime | None:
    """`races.date` + `races.post_time` を JST の datetime にする。

    Args:
        date: 開催日 YYYYMMDD
        post_time: 発走時刻 hhmm（None・不正形式は None を返す）

    Returns:
        発走時刻（JST）。組み立てられなければ None。
    """
    if not post_time or len(post_time) != 4 or not post_time.isdigit():
        return None
    try:
        return datetime.strptime(f"{date}{post_time}", "%Y%m%d%H%M").replace(tzinfo=JST)
    except ValueError:
        return None


def is_snapshot_due(
    post_at: datetime | None,
    now: datetime,
    lead_minutes: int = SNAPSHOT_LEAD_MINUTES,
) -> bool:
    """いま撮るべきレースか。

    条件は「発走前であること」と「発走まで `lead_minutes` 分以内」。

    🔴 **発走時刻を過ぎたレースは撮らない。** 締切間際〜発走後のオッズは
    賭け時点で得られなかった情報を含む（台帳 10.3.1: 確定オッズで選ばれた馬の 87% が
    最終的に1〜3番人気だった）。取りこぼしたレースは記録から欠けるが、
    **後から埋めるより欠けている方が安全**。

    Args:
        post_at: 発走時刻（JST）。None なら対象外。
        now: 現在時刻（tz 付き）。
        lead_minutes: 発走何分前から対象にするか。

    Returns:
        スナップショット対象か。
    """
    if post_at is None:
        return False
    remaining = (post_at - now).total_seconds()
    return 0 < remaining <= lead_minutes * 60


def _index_ranks(pairs: list[tuple[int, float]]) -> dict[int, int]:
    """(馬番, 総合指数) から順位を作る。

    同値は馬番の小さい方を上位（本番 `rank_by_hn` と同じ「先着」規則）。
    """
    ordered = sorted(pairs, key=lambda x: (-x[1], x[0]))
    return {hn: i + 1 for i, (hn, _ci) in enumerate(ordered)}


@dataclass(frozen=True)
class RaceDecision:
    """1レースぶんの判定結果（DB に触らない純粋な計算結果）。"""

    top3_share: float | None
    head_count_used: int | None
    pop_ranks: dict[int, int]
    index_ranks: dict[int, int]
    eligible: list[tuple[int, int]]
    """(馬番, 指数順位) の適格馬。絞り込み前。"""
    picked: list[int]
    """実際に推奨する馬番（最大 `CHIHOU_PICK_MAX_PER_RACE` 頭）。"""
    skip_reason: str | None
    """推奨0のときの理由。推奨が出たときは None。"""


def evaluate_race(
    *,
    win_odds: dict[int, float],
    index_by_hn: dict[int, float],
    head_count: int | None,
    registered_count: int | None,
) -> RaceDecision:
    """1レースの注目馬を判定する。

    ⚠️ 判定そのものは本番と同じ関数（`chihou_is_place_pick` /
    `chihou_select_place_picks`）に委ねる。ここがやるのは入力の組み立てと、
    「なぜ推奨が出なかったか」の分類だけ。

    Args:
        win_odds: 馬番 → 発走前単勝オッズ。
        index_by_hn: 馬番 → 総合指数。
        head_count: 確定出走頭数（発走前は通常 None）。
        registered_count: 登録頭数。

    Returns:
        判定結果。
    """
    share = chihou_market_top3_share(win_odds.values()) if win_odds else None
    pop_ranks = chihou_popularity_ranks(win_odds)
    ranks = _index_ranks(list(index_by_hn.items()))
    head_used = chihou_effective_head_count(head_count, registered_count)

    eligible = [
        (hn, ranks[hn])
        for hn in sorted(set(ranks) | set(win_odds))
        if chihou_is_place_pick(pop_ranks.get(hn), ranks.get(hn), share, head_used)
    ]
    picked = chihou_select_place_picks(eligible)

    if not win_odds:
        skip_reason = "no_odds"
    elif not index_by_hn:
        skip_reason = "no_index"
    elif head_used is None:
        skip_reason = "no_head_count"
    elif head_used < CHIHOU_PLACE_MIN_HEAD_COUNT:
        skip_reason = "small_field"
    elif share is None or share >= CHIHOU_OPEN_RACE_MAX_TOP3_SHARE:
        skip_reason = "closed_race"
    elif not eligible:
        skip_reason = "no_candidate"
    else:
        skip_reason = None

    return RaceDecision(
        top3_share=share,
        head_count_used=head_used,
        pop_ranks=pop_ranks,
        index_ranks=ranks,
        eligible=eligible,
        picked=picked,
        skip_reason=skip_reason,
    )


async def snapshot_place_picks(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    lead_minutes: int = SNAPSHOT_LEAD_MINUTES,
) -> dict[str, int]:
    """発走直前のレースを記録する。毎分 cron から呼ぶ。

    Args:
        db: セッション。
        now: 現在時刻（テスト用に注入可）。既定は JST の現在時刻。
        lead_minutes: 発走何分前から対象にするか。

    Returns:
        `{"races": 記録したレース数, "picks": 記録した推奨頭数, "horses": 記録した行数}`
    """
    now = now or datetime.now(JST)
    date = now.astimezone(JST).strftime("%Y%m%d")

    races_result = await db.execute(
        select(ChihouRace)
        .where(ChihouRace.date == date)
        .where(ChihouRace.course != BANEI_COURSE_CODE)
    )
    due = [
        r for r in races_result.scalars().all()
        if is_snapshot_due(parse_post_datetime(r.date, r.post_time), now, lead_minutes)
    ]
    if not due:
        return {"races": 0, "picks": 0, "horses": 0}

    # 既に撮ってあるレースは触らない（毎分 cron なので同じレースが何度も窓に入る）
    logged = await db.execute(
        select(ChihouPlacePickRace.race_id).where(
            ChihouPlacePickRace.race_id.in_([r.id for r in due])
        )
    )
    already = set(logged.scalars().all())
    targets = [r for r in due if r.id not in already]
    if not targets:
        return {"races": 0, "picks": 0, "horses": 0}

    race_ids = [r.id for r in targets]
    odds_rows = await db.execute(
        sql_text(LATEST_ODDS_SQL.format(bet_types="'win', 'place'")),
        {"race_ids": race_ids},
    )
    win_by_race: dict[int, dict[int, float]] = defaultdict(dict)
    place_by_race: dict[int, dict[int, float]] = defaultdict(dict)
    for rid, bet_type, combo, odds_val in odds_rows.all():
        if odds_val is None or not str(combo).isdigit():
            continue
        target = win_by_race if bet_type == "win" else place_by_race
        target[int(rid)][int(combo)] = float(odds_val)

    entry_rows = await db.execute(
        select(
            ChihouRaceEntry.race_id,
            ChihouRaceEntry.horse_number,
            ChihouRaceEntry.horse_id,
            ChihouHorse.name,
        )
        .join(ChihouHorse, ChihouHorse.id == ChihouRaceEntry.horse_id)
        .where(ChihouRaceEntry.race_id.in_(race_ids))
    )
    entries_by_race: dict[int, list[tuple[int, int, str | None]]] = defaultdict(list)
    for rid, hn, hid, name in entry_rows.all():
        if hn is None:
            continue
        entries_by_race[int(rid)].append((int(hn), int(hid), name))

    idx_rows = await db.execute(
        select(
            ChihouCalculatedIndex.race_id,
            ChihouRaceEntry.horse_number,
            ChihouCalculatedIndex.composite_index,
            ChihouCalculatedIndex.win_probability,
            ChihouCalculatedIndex.place_probability,
        )
        .join(
            ChihouRaceEntry,
            (ChihouRaceEntry.race_id == ChihouCalculatedIndex.race_id)
            & (ChihouRaceEntry.horse_id == ChihouCalculatedIndex.horse_id),
        )
        .where(ChihouCalculatedIndex.race_id.in_(race_ids))
        .where(ChihouCalculatedIndex.version == CHIHOU_COMPOSITE_VERSION)
    )
    idx_by_race: dict[int, dict[int, tuple[float, float | None, float | None]]] = defaultdict(dict)
    for rid, hn, ci, wp, pp in idx_rows.all():
        if hn is None or ci is None:
            continue
        idx_by_race[int(rid)][int(hn)] = (float(ci), wp, pp)

    n_races = n_picks = n_horses = 0
    for race in targets:
        win_odds = win_by_race.get(race.id, {})
        place_odds = place_by_race.get(race.id, {})
        idx = idx_by_race.get(race.id, {})
        entries = entries_by_race.get(race.id, [])
        decision = evaluate_race(
            win_odds=win_odds,
            index_by_hn={hn: v[0] for hn, v in idx.items()},
            head_count=race.head_count,
            registered_count=race.registered_count,
        )
        pick_order = {hn: i + 1 for i, hn in enumerate(decision.picked)}
        share = decision.top3_share

        post_at = parse_post_datetime(race.date, race.post_time)
        # 毎分 cron なので、前の実行が長引くと 2 本が同じレースを同時に狙いうる。
        # 上の重複チェックは check-then-act で、実際の防波堤は race_id の UNIQUE 制約。
        # ON CONFLICT DO NOTHING にして、負けた側はそのレースだけ静かに捨てる
        # （例外にするとバッチ全体が巻き添えになり、同じ分の他レースまで撮り逃す）。
        inserted = await db.execute(
            pg_insert(ChihouPlacePickRace)
            .values(
                date=race.date,
                race_id=race.id,
                course_name=race.course_name,
                race_number=race.race_number,
                post_time=race.post_time,
                snapshot_at=now.astimezone(UTC),
                lead_minutes=(
                    int((post_at - now).total_seconds() // 60) if post_at is not None else None
                ),
                index_version=CHIHOU_COMPOSITE_VERSION,
                rule_version=PICK_RULE_VERSION,
                head_count_used=decision.head_count_used,
                head_count_provisional=race.head_count is None,
                top3_share=round(share, 4) if share is not None else None,
                n_entries=len(entries),
                n_odds=len(win_odds),
                n_eligible=len(decision.eligible),
                n_picked=len(decision.picked),
                skip_reason=decision.skip_reason,
            )
            .on_conflict_do_nothing(index_elements=["race_id"])
            .returning(ChihouPlacePickRace.id)
        )
        pick_race_id = inserted.scalar_one_or_none()
        if pick_race_id is None:
            logger.info(
                "[chihou place-pick snapshot] race_id=%s は既に記録済み（同時実行）", race.id
            )
            continue

        name_by_hn = {hn: name for hn, _hid, name in entries}
        hid_by_hn = {hn: hid for hn, hid, _name in entries}
        eligible_hns = {hn for hn, _r in decision.eligible}
        for hn in sorted({*name_by_hn, *idx, *win_odds}):
            ci, wp, pp = idx.get(hn, (None, None, None))
            db.add(
                ChihouPlacePick(
                    pick_race_id=pick_race_id,
                    race_id=race.id,
                    horse_id=hid_by_hn.get(hn),
                    horse_number=hn,
                    horse_name=name_by_hn.get(hn),
                    composite_index=ci,
                    index_rank=decision.index_ranks.get(hn),
                    win_probability=wp,
                    place_probability=pp,
                    pre_win_odds=win_odds.get(hn),
                    pre_place_odds=place_odds.get(hn),
                    pop_rank=decision.pop_ranks.get(hn),
                    is_eligible=hn in eligible_hns,
                    is_picked=hn in pick_order,
                    pick_order=pick_order.get(hn),
                )
            )
            n_horses += 1
        n_races += 1
        n_picks += len(decision.picked)

    await db.commit()
    logger.info(
        "[chihou place-pick snapshot] %d レース / 推奨 %d 頭 / %d 行 (date=%s)",
        n_races, n_picks, n_horses, date,
    )
    return {"races": n_races, "picks": n_picks, "horses": n_horses}


async def settle_place_picks(db: AsyncSession, date: str) -> dict[str, int]:
    """指定日のスナップショットに確定結果を書き戻す。

    冪等。未確定のレースは触らない（`settled_at` が NULL のまま残り、翌日以降の
    実行で拾われる）。

    Args:
        db: セッション。
        date: 対象日 YYYYMMDD。

    Returns:
        `{"races": 確定させたレース数, "horses": 更新した馬の行数}`
    """
    races_result = await db.execute(
        select(ChihouPlacePickRace)
        .where(ChihouPlacePickRace.date == date)
        .where(ChihouPlacePickRace.settled_at.is_(None))
    )
    log_races = list(races_result.scalars().all())
    if not log_races:
        return {"races": 0, "horses": 0}

    race_ids = [lr.race_id for lr in log_races]
    result_rows = await db.execute(
        select(ChihouRaceResult).where(ChihouRaceResult.race_id.in_(race_ids))
    )
    results: dict[int, dict[int, ChihouRaceResult]] = defaultdict(dict)
    for rr in result_rows.scalars().all():
        if rr.horse_number is not None:
            results[rr.race_id][int(rr.horse_number)] = rr

    pick_rows = await db.execute(
        select(ChihouPlacePick).where(ChihouPlacePick.race_id.in_(race_ids))
    )
    picks_by_race: dict[int, list[ChihouPlacePick]] = defaultdict(list)
    for p in pick_rows.scalars().all():
        picks_by_race[p.race_id].append(p)

    now = datetime.now(UTC)
    n_races = n_horses = 0
    for lr in log_races:
        race_results = results.get(lr.race_id)
        if not race_results:
            continue  # まだ確定していない
        # 着順が1つも入っていない段階の行（速報の途中）では確定させない
        finished = [
            rr for rr in race_results.values()
            if rr.finish_position is not None and (rr.abnormality_code or 0) == 0
        ]
        if not finished:
            continue

        placed_pop_ranks: list[int] = []
        for p in picks_by_race.get(lr.race_id, []):
            res = race_results.get(p.horse_number)
            if res is None:
                continue
            p.finish_position = res.finish_position
            p.abnormality_code = res.abnormality_code
            p.final_win_odds = float(res.win_odds) if res.win_odds is not None else None
            p.final_win_popularity = res.win_popularity
            p.place_payout_odds = float(res.place_odds) if res.place_odds is not None else None
            p.settled_at = now
            n_horses += 1
            if (
                p.pop_rank is not None
                and p.pop_rank >= UPSET_POP_RANK
                and res.finish_position is not None
                and res.finish_position <= 3
                and (res.abnormality_code or 0) == 0
            ):
                placed_pop_ranks.append(p.pop_rank)

        picked = [p for p in picks_by_race.get(lr.race_id, []) if p.is_picked]
        lr.n_finishers = len(finished)
        # 推奨が無いレースは「外れ」ではなく「対象外」。False と NULL を混ぜない
        lr.race_hit = any(
            p.finish_position is not None
            and p.finish_position <= 3
            and (p.abnormality_code or 0) == 0
            for p in picked
        ) if picked else None
        # 発走前オッズが1頭も取れていなければ人気薄の定義自体が作れない → 不明のまま残す
        has_pop = any(p.pop_rank is not None for p in picks_by_race.get(lr.race_id, []))
        lr.upset_placed = bool(placed_pop_ranks) if has_pop else None
        lr.settled_at = now
        n_races += 1

    await db.commit()
    logger.info(
        "[chihou place-pick settle] date=%s 確定 %d レース / %d 頭", date, n_races, n_horses
    )
    return {"races": n_races, "horses": n_horses}
