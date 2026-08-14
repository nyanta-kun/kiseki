"""JRA 推奨（hit_tier）の**前向き記録**（発走前スナップショット → 翌日確定）。

## なぜ要るか

現行の推奨は「1レース1推奨 = 指数1位馬 + tier(S/A/B/C+/C)」で、C は見送り。
この運用点には**確認窓が無い**。2026年の窓は tier の再設計・v27 の係数選定・
着外率の閾値選定などで既に何度も使われている（`scripts/JRA_TEST_USAGE_LEDGER.md`）。

しかも**後付けの集計ができない**。理由は 3 つあり、どれか 1 つでも致命的:

1. `/api/recommendations` は**都度算出**で DB に何も残さない
2. `keiba.calculated_indices` の現行 version 行は馬体重到着ごとに上書きされ、
   さらにバックフィルで丸ごと置き換わる（台帳 5.2）
3. tier の第一分岐 `market_agree`（指数1位が単勝1番人気か）は**発走直前まで動く**。
   実測（2026-08 の4開催日・144レース）:

   | 発走何分前 | オッズあり | 1番人気が確定と一致 |
   |---|---|---|
   | 30分 | 138/144 | 72.5% |
   | 15分 | 140/144 | 77.1% |
   | **10分** | **140/144** | **80.7%** |
   | 5分 | 141/144 | 83.7% |
   | 2分 | 141/144 | 86.5% |

   **確定オッズから tier を作り直しても、ユーザーが見た tier とは 2 割ずれる。**

そこで 2 段構成にする:

1. `snapshot_hit_tier()` — 発走 `SNAPSHOT_LEAD_MINUTES` 分前に、そのレースの
   全出走馬の指数・発走前オッズ・tier 判定の入力と結果を保存する（毎分 cron）
2. `settle_hit_tier()` — 翌日、確定結果を書き戻す。**確定オッズでの tier も同時に
   計算して保存する**ので、「発走前 tier」と「確定 tier」の差＝オッズの動きが
   後から追加コスト無しで測れる（日次 cron）

## 記録の範囲を欲張っている理由

- **推奨が出なかったレース（tier=C）も記録する。** hit_tier は C を見送るので、
  棄権側が無いと「見送って正解だったか」を一切測れない
- **推奨馬だけでなく全出走馬を記録する。** 指数が上書きされる以上、
  「tier の閾値を変えていたら」「指数2位も買っていたら」の事後評価は、
  全馬ぶんがここに残っていなければ二度とできない

⚠️ **判定は本番と同じ関数**（`calculate_race_confidence` / `is_market_favorite` /
`calculate_market_chaos` / `calculate_recommend_rank`）を呼ぶこと。
条件をこのモジュールに書き写すと、本番の閾値を変えたときに記録だけが古い条件で残る。
閾値は `rule_version` として毎行に埋まるので、変更しても世代が自動で分かれる。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CalculatedIndex,
    HitTierPick,
    HitTierRace,
    Horse,
    OddsHistory,
    Race,
    RaceEntry,
    RaceResult,
)
from ..indices.composite import COMPOSITE_VERSION, OUT_PROB_CUTOFF
from ..indices.confidence import (
    ENTROPY_THRESHOLDS,
    JRA_GAP_FULL_SCORE,
    calculate_market_chaos,
    calculate_race_confidence,
    calculate_recommend_rank,
    is_market_favorite,
)
from .recommender import _HIT_TIER_BET

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

JRA_COURSE_CODES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

# 発走の何分前を「その日の判断」として記録するか。
#
# 上表のとおり 1番人気は発走直前まで動く。10分前を採ったのは
#   - **賭けられる時点である**こと（締切は発走1〜2分前。30分前だと早すぎ、
#     2分前だと「見て買う」時間が無い）
#   - オッズ充足がほぼ頭打ちになる（140/144。5分前でも141で大差ない）
#   - 一致率 80.7% と、そこから先の改善が緩やか（5分前 83.7 / 2分前 86.5）
# の 3 点による。毎分 cron なので実際に撮れるのは 9〜10 分前。
SNAPSHOT_LEAD_MINUTES: int = 10

# 判定ルールの署名。閾値を変えたら自動的に値が変わるので、集計時に世代を混ぜずに済む。
RULE_VERSION: str = (
    f"hit_tier,gap={JRA_GAP_FULL_SCORE},"
    f"entC={ENTROPY_THRESHOLDS.get('C')},"
    f"cut={OUT_PROB_CUTOFF}"
)


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

    🔴 **発走時刻を過ぎたレースは撮らない。** 締切間際〜発走後のオッズは
    賭け時点で得られなかった情報を含む。取りこぼしたレースは記録から欠けるが、
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


def popularity_ranks(win_odds: dict[int, float]) -> dict[int, int]:
    """単勝オッズから人気順位を作る（同値は馬番の小さい方を上位）。"""
    ordered = sorted(win_odds.items(), key=lambda x: (x[1], x[0]))
    return {hn: i + 1 for i, (hn, _o) in enumerate(ordered)}


def index_ranks(index_by_hn: dict[int, float]) -> dict[int, int]:
    """総合指数から順位を作る（降順・同値は馬番の小さい方を上位）。"""
    ordered = sorted(index_by_hn.items(), key=lambda x: (-x[1], x[0]))
    return {hn: i + 1 for i, (hn, _v) in enumerate(ordered)}


@dataclass(frozen=True)
class TierDecision:
    """1レースぶんの tier 判定（DB に触らない純粋な計算結果）。"""

    tier: str | None
    bet_type: str | None
    confidence_score: int | None
    win_prob_top: float | None
    entropy_norm: float | None
    market_agree: bool | None
    top1_horse_number: int | None
    top1_win_odds: float | None
    index_ranks: dict[int, int]
    pop_ranks: dict[int, int]
    skip_reason: str | None


def evaluate_race(
    *,
    win_odds: dict[int, float],
    index_by_hn: dict[int, float],
    win_probs: dict[int, float],
    head_count: int | None,
) -> TierDecision:
    """1レースの tier を判定する。

    ⚠️ 判定そのものは本番と同じ関数に委ねる。ここがやるのは入力の組み立てと、
    「なぜ推奨が出なかったか」の分類だけ。

    Args:
        win_odds: 馬番 → 発走前単勝オッズ。
        index_by_hn: 馬番 → 総合指数。
        win_probs: 馬番 → 勝率（confidence の分散スコア入力）。
        head_count: 出走頭数（発走前は None のことが多い）。

    Returns:
        判定結果。
    """
    ranks = index_ranks(index_by_hn)
    pops = popularity_ranks(win_odds)

    if not index_by_hn:
        return TierDecision(None, None, None, None, None, None, None, None,
                            ranks, pops, "no_index")

    top1_hn = min(ranks, key=lambda hn: ranks[hn])
    top_odds = win_odds.get(top1_hn)
    all_odds = list(win_odds.values())

    conf = calculate_race_confidence(
        list(index_by_hn.values()),
        head_count,
        list(win_probs.values()) or None,
        gap_full_score=JRA_GAP_FULL_SCORE,
    )
    market_agree = is_market_favorite(top_odds, all_odds or None)
    entropy_norm = calculate_market_chaos(all_odds).get("entropy_norm")
    tier = calculate_recommend_rank(
        conf["score"], conf.get("win_prob_top"), top_odds, market_agree, entropy_norm
    )

    if not win_odds:
        skip_reason = "no_odds"
    elif tier == "C":
        skip_reason = "tier_c"
    else:
        skip_reason = None

    return TierDecision(
        tier=tier,
        bet_type=_HIT_TIER_BET.get(tier),
        confidence_score=conf["score"],
        win_prob_top=conf.get("win_prob_top"),
        entropy_norm=entropy_norm,
        market_agree=market_agree,
        top1_horse_number=top1_hn,
        top1_win_odds=top_odds,
        index_ranks=ranks,
        pop_ranks=pops,
        skip_reason=skip_reason,
    )


async def _latest_win_place_odds(
    db: AsyncSession, race_ids: list[int]
) -> tuple[dict[int, dict[int, float]], dict[int, dict[int, float]]]:
    """対象レースの最新 win/place オッズを返す。

    ⚠️ **`fetched_at` は UTC・DB のセッション TZ は Asia/Tokyo** という食い違いがある
    （台帳 14.2）。`now()` と直接比べてはいけないので、**最大時刻からの相対**で絞る
    （`recommender._collect_race_data` と同じ手）。
    """
    if not race_ids:
        return {}, {}
    latest = (
        await db.execute(
            select(func.max(OddsHistory.fetched_at)).where(
                OddsHistory.race_id.in_(race_ids)
            )
        )
    ).scalar()
    time_filter = (
        [OddsHistory.fetched_at >= latest - timedelta(minutes=5)]
        if latest is not None
        else []
    )
    rows = await db.execute(
        select(
            OddsHistory.race_id,
            OddsHistory.bet_type,
            OddsHistory.combination,
            OddsHistory.odds,
        )
        .where(
            OddsHistory.race_id.in_(race_ids),
            OddsHistory.bet_type.in_(["win", "place"]),
            *time_filter,
        )
        .distinct(OddsHistory.race_id, OddsHistory.bet_type, OddsHistory.combination)
        .order_by(
            OddsHistory.race_id,
            OddsHistory.bet_type,
            OddsHistory.combination,
            OddsHistory.fetched_at.desc(),
        )
    )
    win: dict[int, dict[int, float]] = defaultdict(dict)
    place: dict[int, dict[int, float]] = defaultdict(dict)
    for rid, bet_type, combo, odds_val in rows.all():
        if odds_val is None or not str(combo).isdigit():
            continue
        target = win if bet_type == "win" else place
        target[int(rid)][int(combo)] = float(odds_val)
    return win, place


async def snapshot_hit_tier(
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
        `{"races": 記録レース数, "recommended": 推奨が出たレース数, "horses": 行数}`
    """
    now = now or datetime.now(JST)
    date = now.astimezone(JST).strftime("%Y%m%d")

    races = (
        await db.execute(
            select(Race)
            .where(Race.date == date)
            .where(Race.course.in_(list(JRA_COURSE_CODES)))
        )
    ).scalars().all()
    due = [
        r for r in races
        if is_snapshot_due(parse_post_datetime(r.date, r.post_time), now, lead_minutes)
    ]
    if not due:
        return {"races": 0, "recommended": 0, "horses": 0}

    # 既に撮ってあるレースは触らない（毎分 cron なので同じレースが何度も窓に入る）
    already = set(
        (
            await db.execute(
                select(HitTierRace.race_id).where(
                    HitTierRace.race_id.in_([r.id for r in due])
                )
            )
        ).scalars().all()
    )
    targets = [r for r in due if r.id not in already]
    if not targets:
        return {"races": 0, "recommended": 0, "horses": 0}

    race_ids = [r.id for r in targets]
    win_by_race, place_by_race = await _latest_win_place_odds(db, race_ids)

    entry_rows = await db.execute(
        select(RaceEntry.race_id, RaceEntry.horse_number, RaceEntry.horse_id, Horse.name)
        .join(Horse, Horse.id == RaceEntry.horse_id)
        .where(RaceEntry.race_id.in_(race_ids))
    )
    entries_by_race: dict[int, list[tuple[int, int, str | None]]] = defaultdict(list)
    for rid, hn, hid, name in entry_rows.all():
        if hn is not None:
            entries_by_race[int(rid)].append((int(hn), int(hid), name))

    idx_rows = await db.execute(
        select(
            CalculatedIndex.race_id,
            RaceEntry.horse_number,
            CalculatedIndex.composite_index,
            CalculatedIndex.win_probability,
            CalculatedIndex.place_probability,
            CalculatedIndex.out_probability,
        )
        .join(
            RaceEntry,
            (RaceEntry.race_id == CalculatedIndex.race_id)
            & (RaceEntry.horse_id == CalculatedIndex.horse_id),
        )
        .where(CalculatedIndex.race_id.in_(race_ids))
        .where(CalculatedIndex.version == COMPOSITE_VERSION)
    )
    idx_by_race: dict[int, dict[int, tuple]] = defaultdict(dict)
    for rid, hn, ci, wp, pp, op_ in idx_rows.all():
        if hn is None or ci is None:
            continue
        idx_by_race[int(rid)][int(hn)] = (float(ci), wp, pp, op_)

    n_races = n_reco = n_horses = 0
    for race in targets:
        win_odds = win_by_race.get(race.id, {})
        place_odds = place_by_race.get(race.id, {})
        idx = idx_by_race.get(race.id, {})
        entries = entries_by_race.get(race.id, [])

        decision = evaluate_race(
            win_odds=win_odds,
            index_by_hn={hn: v[0] for hn, v in idx.items()},
            win_probs={hn: float(v[1]) for hn, v in idx.items() if v[1] is not None},
            head_count=race.head_count,
        )

        post_at = parse_post_datetime(race.date, race.post_time)
        # 毎分 cron なので、前の実行が長引くと 2 本が同じレースを同時に狙いうる。
        # 上の重複チェックは check-then-act で、実際の防波堤は race_id の UNIQUE 制約。
        # ON CONFLICT DO NOTHING にして、負けた側はそのレースだけ静かに捨てる
        # （例外にするとバッチ全体が巻き添えになり、同じ分の他レースまで撮り逃す）。
        inserted = await db.execute(
            pg_insert(HitTierRace)
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
                index_version=COMPOSITE_VERSION,
                rule_version=RULE_VERSION,
                head_count=race.head_count,
                n_entries=len(entries),
                n_odds=len(win_odds),
                confidence_score=decision.confidence_score,
                win_prob_top=decision.win_prob_top,
                entropy_norm=decision.entropy_norm,
                market_agree=decision.market_agree,
                top1_win_odds=decision.top1_win_odds,
                tier=decision.tier,
                bet_type=decision.bet_type,
                is_recommended=decision.skip_reason is None,
                skip_reason=decision.skip_reason,
            )
            .on_conflict_do_nothing(index_elements=["race_id"])
            .returning(HitTierRace.id)
        )
        pick_race_id = inserted.scalar_one_or_none()
        if pick_race_id is None:
            logger.info("[jra hit-tier snapshot] race_id=%s は既に記録済み（同時実行）", race.id)
            continue

        name_by_hn = {hn: name for hn, _hid, name in entries}
        hid_by_hn = {hn: hid for hn, hid, _name in entries}
        for hn in sorted({*name_by_hn, *idx, *win_odds}):
            ci, wp, pp, op_ = idx.get(hn, (None, None, None, None))
            db.add(
                HitTierPick(
                    pick_race_id=pick_race_id,
                    race_id=race.id,
                    horse_id=hid_by_hn.get(hn),
                    horse_number=hn,
                    horse_name=name_by_hn.get(hn),
                    composite_index=ci,
                    index_rank=decision.index_ranks.get(hn),
                    win_probability=wp,
                    place_probability=pp,
                    out_probability=op_,
                    is_cut_off=(float(op_) >= OUT_PROB_CUTOFF) if op_ is not None else None,
                    pre_win_odds=win_odds.get(hn),
                    pre_place_odds=place_odds.get(hn),
                    pop_rank=decision.pop_ranks.get(hn),
                    is_top1=hn == decision.top1_horse_number,
                    is_recommended=(
                        decision.skip_reason is None and hn == decision.top1_horse_number
                    ),
                )
            )
            n_horses += 1
        n_races += 1
        n_reco += 1 if decision.skip_reason is None else 0

    await db.commit()
    logger.info(
        "[jra hit-tier snapshot] %d レース / 推奨 %d / %d 行 (date=%s)",
        n_races, n_reco, n_horses, date,
    )
    return {"races": n_races, "recommended": n_reco, "horses": n_horses}


async def settle_hit_tier(db: AsyncSession, date: str) -> dict[str, int]:
    """指定日のスナップショットに確定結果を書き戻す。

    冪等。未確定のレースは触らない（`settled_at` が NULL のまま残り、翌日以降の
    実行で拾われる）。

    確定オッズでの `market_agree` / tier も同時に計算して保存する。
    **これが「発走前 tier」と「確定 tier」の比較を後から可能にする唯一の手段**で、
    発走10分前の1番人気は確定の 80.7% しか一致しない（モジュール docstring）。

    Args:
        db: セッション。
        date: 対象日 YYYYMMDD。

    Returns:
        `{"races": 確定レース数, "horses": 更新した馬の行数}`
    """
    log_races = list(
        (
            await db.execute(
                select(HitTierRace)
                .where(HitTierRace.date == date)
                .where(HitTierRace.settled_at.is_(None))
            )
        ).scalars().all()
    )
    if not log_races:
        return {"races": 0, "horses": 0}

    race_ids = [lr.race_id for lr in log_races]
    results: dict[int, dict[int, RaceResult]] = defaultdict(dict)
    entry_hn: dict[tuple[int, int], int] = {}
    for rid, hn, hid in (
        await db.execute(
            select(RaceEntry.race_id, RaceEntry.horse_number, RaceEntry.horse_id)
            .where(RaceEntry.race_id.in_(race_ids))
        )
    ).all():
        if hn is not None:
            entry_hn[(int(rid), int(hid))] = int(hn)
    for rr in (
        await db.execute(select(RaceResult).where(RaceResult.race_id.in_(race_ids)))
    ).scalars().all():
        hn = entry_hn.get((rr.race_id, rr.horse_id))
        if hn is not None:
            results[rr.race_id][hn] = rr

    picks_by_race: dict[int, list[HitTierPick]] = defaultdict(list)
    for p in (
        await db.execute(select(HitTierPick).where(HitTierPick.race_id.in_(race_ids)))
    ).scalars().all():
        picks_by_race[p.race_id].append(p)

    now = datetime.now(UTC)
    n_races = n_horses = 0
    for lr in log_races:
        race_results = results.get(lr.race_id)
        if not race_results:
            continue  # まだ確定していない
        finished = [
            rr for rr in race_results.values()
            if rr.finish_position is not None and (rr.abnormality_code or 0) == 0
        ]
        if not finished:
            continue

        picks = picks_by_race.get(lr.race_id, [])
        final_odds: dict[int, float] = {}
        for p in picks:
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
            if p.final_win_odds is not None:
                final_odds[p.horse_number] = p.final_win_odds

        top1 = next((p for p in picks if p.is_top1), None)
        lr.n_finishers = len(finished)
        lr.top1_finish_position = top1.finish_position if top1 else None
        # 推奨が無いレース（tier=C 等）は「外れ」ではなく「対象外」。False と NULL を混ぜない
        if lr.is_recommended and top1 is not None and top1.finish_position is not None:
            need = 1 if lr.bet_type == "win" else 3
            lr.hit = (
                top1.finish_position <= need
                and (top1.abnormality_code or 0) == 0
            )
        else:
            lr.hit = None

        # 確定オッズで tier を作り直す（発走前 tier との差＝オッズの動き）
        if top1 is not None and final_odds:
            lr.final_market_agree = is_market_favorite(
                final_odds.get(top1.horse_number), list(final_odds.values())
            )
            lr.final_tier = calculate_recommend_rank(
                lr.confidence_score or 0,
                lr.win_prob_top,
                final_odds.get(top1.horse_number),
                lr.final_market_agree,
                lr.entropy_norm,
            )
        lr.settled_at = now
        n_races += 1

    await db.commit()
    logger.info("[jra hit-tier settle] date=%s 確定 %d レース / %d 頭", date, n_races, n_horses)
    return {"races": n_races, "horses": n_horses}
