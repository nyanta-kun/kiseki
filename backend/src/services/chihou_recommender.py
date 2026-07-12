"""地方競馬 推奨レース・馬券のソース提供 / 提出（Claude定期実行用）

Claude.ai 定期エージェントが以下を行う：
1. GET /api/chihou/recommendations/source?date=YYYYMMDD
2. 条件を満たすすべてのレースを推奨として選定（オッズなし、指数・信頼度・競馬場特性のみ。件数上限なし）
3. POST /api/chihou/recommendations/submit?date=YYYYMMDD

このサービスは Anthropic API を呼び出さない（API課金なし）。
発走10分前にオッズを取得してbuy/pass判断を更新する処理は維持する。
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import (
    ChihouCalculatedIndex,
    ChihouHorse,
    ChihouOddsHistory,
    ChihouRace,
    ChihouRaceEntry,
    ChihouRacePayout,
    ChihouRaceRecommendation,
    ChihouRaceResult,
)
from ..indices.buy_signal import (
    CHIHOU_PLACE_BET_FAV_ODDS_MAX,
    chihou_is_place_bet,
    chihou_is_sweet_spot,
    chihou_low_odds_trust_level,
)
from ..indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION as _CHIHOU_COMPOSITE_VERSION
from ..indices.chihou_upset import get_chihou_upset_reranker

logger = logging.getLogger(__name__)

# buy/pass判断の期待値閾値
_BUY_EV_THRESHOLD = 1.0


def calc_race_concentration(place_probs: list[float]) -> dict[str, object]:
    """レース内の複勝確率集中度を計算する。

    ⚠️ 閾値再較正 (2026-06-05): Phase2 で place_probability を較正(is_top3)に
    変えた結果、旧閾値(>0.873/>0.715)では全レースが "low" に張り付いて定数化した。
    新較正確率での OOS検証(2025.7-2026.6, 10,883R)の top2_share 五分位 ×
    1位複勝率に基づき再較正:
      top2_share > 0.42 → high   (1位複勝率 ~82%)
      top2_share > 0.36 → medium (1位複勝率 ~69-73%)
      それ以下          → low    (1位複勝率 ~53-61%)
    """
    if len(place_probs) < 2:
        return {"top2_share": None, "hhi": None, "confidence_level": None}

    total = sum(place_probs)
    if total <= 0:
        return {"top2_share": None, "hhi": None, "confidence_level": None}

    shares = sorted([p / total for p in place_probs], reverse=True)
    top2_share = shares[0] + shares[1]
    hhi = sum(s * s for s in shares)

    if top2_share > 0.42:
        confidence_level = "high"
    elif top2_share > 0.36:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    return {
        "top2_share": round(top2_share, 3),
        "hhi": round(hhi, 4),
        "confidence_level": confidence_level,
    }

_DANTEIKEI = [
    re.compile(r"だ$"),
    re.compile(r"だろう$"),
    re.compile(r"である$"),
    re.compile(r"と言える$"),
    re.compile(r"といえる$"),
    re.compile(r"と見る$"),
    re.compile(r"たい$"),
]


def _to_taigen_dome(text: str) -> str:
    """文末を体言止めに変換する。"""
    parts = text.split("。")
    result = []
    for part in parts:
        s = part.strip()
        if not s:
            continue
        for pat in _DANTEIKEI:
            s = pat.sub("", s)
        result.append(s)
    joined = "。".join(result)
    if text.endswith("。"):
        joined += "。"
    return joined


async def _fetch_external_raw(
    session: AsyncSession, race_ids: list[int]
) -> dict[int, dict[int, tuple[float | None, float | None]]]:
    """sekito.kichiuma / sekito.netkeiba の生スコアを馬単位で取得する。

    Returns:
        dict[race_id, dict[horse_number, (kichiuma sp_score, netkeiba idx_ave)]]
    """
    if not race_ids:
        return {}

    sql = text("""
        SELECT
            r.id AS race_id,
            re.horse_number,
            k.sp_score,
            CASE
                WHEN n.idx_ave ~ '^-?[0-9]+\\*?$'
                THEN regexp_replace(n.idx_ave, '\\*', '')::float
                ELSE NULL
            END AS idx_ave
        FROM chihou.races r
        JOIN sekito.racecourse rc ON r.course = rc.netkeiba_id
        JOIN chihou.race_entries re ON re.race_id = r.id
        LEFT JOIN sekito.kichiuma k
            ON k.date = TO_DATE(r.date, 'YYYYMMDD')
            AND k.course_code = rc.code
            AND k.race_no = r.race_number
            AND k.horse_no = re.horse_number
        LEFT JOIN sekito.netkeiba n
            ON n.date = TO_DATE(r.date, 'YYYYMMDD')
            AND n.course_code = rc.code
            AND n.race_no = r.race_number
            AND n.horse_no = re.horse_number
            AND n.is_time_index = true
        WHERE r.id = ANY(:race_ids)
        ORDER BY r.id, re.horse_number
    """)
    rows = (await session.execute(sql, {"race_ids": race_ids})).fetchall()

    # race_id → {horse_number: (sp_score, idx_ave)}
    raw: dict[int, dict[int, tuple[float | None, float | None]]] = {}
    for race_id, horse_number, sp_score, idx_ave in rows:
        raw.setdefault(race_id, {})[horse_number] = (
            float(sp_score) if sp_score is not None else None,
            float(idx_ave) if idx_ave is not None else None,
        )
    return raw


async def _fetch_external_consensus(
    session: AsyncSession, race_ids: list[int]
) -> dict[int, dict[int, int]]:
    """sekito.kichiuma / sekito.netkeiba から外部指数コンセンサスを取得する。

    Returns:
        dict[race_id, dict[horse_number, external_consensus]]
        external_consensus: 0〜2（kichiuma/netkeibaのうち何本が1位と一致するか）
        レース自体に外部データが存在しない場合は race_id がキーに含まれない。
    """
    raw = await _fetch_external_raw(session, race_ids)

    result: dict[int, dict[int, int]] = {}
    for race_id, horse_map in raw.items():
        # どちらのデータも全馬 None ならこのレースはスキップ（外部データなし）
        has_kichi = any(v[0] is not None for v in horse_map.values())
        has_netk = any(v[1] is not None for v in horse_map.values())
        if not has_kichi and not has_netk:
            continue

        # kichiuma 最高 sp_score 馬番
        kichi_top: int | None = None
        if has_kichi:
            kichi_entries = [(hn, v[0]) for hn, v in horse_map.items() if v[0] is not None]
            kichi_top = max(kichi_entries, key=lambda x: x[1])[0] if kichi_entries else None
        # netkeiba 最高 idx_ave 馬番
        netk_top: int | None = None
        if has_netk:
            netk_entries = [(hn, v[1]) for hn, v in horse_map.items() if v[1] is not None]
            netk_top = max(netk_entries, key=lambda x: x[1])[0] if netk_entries else None

        consensus: dict[int, int] = {}
        for hn in horse_map:
            score = (1 if hn == kichi_top else 0) + (1 if hn == netk_top else 0)
            consensus[hn] = score
        result[race_id] = consensus

    return result


async def _collect_chihou_race_data(session: AsyncSession, date: str) -> list[dict[str, Any]]:
    """当日の地方競馬レースデータを指数のみで収集（オッズなし）。"""
    races_result = await session.execute(
        select(ChihouRace)
        .where(ChihouRace.date == date, ChihouRace.course != "83")  # ばんえい除外
        .order_by(ChihouRace.post_time, ChihouRace.id)
    )
    races = races_result.scalars().all()
    if not races:
        return []

    race_ids = [r.id for r in races]

    # 指数 + エントリー + 馬名
    indices_result = await session.execute(
        select(ChihouCalculatedIndex, ChihouRaceEntry, ChihouHorse)
        .join(
            ChihouRaceEntry,
            (ChihouRaceEntry.race_id == ChihouCalculatedIndex.race_id)
            & (ChihouRaceEntry.horse_id == ChihouCalculatedIndex.horse_id),
        )
        .join(ChihouHorse, ChihouHorse.id == ChihouCalculatedIndex.horse_id)
        .where(
            ChihouCalculatedIndex.race_id.in_(race_ids),
            ChihouCalculatedIndex.version == _CHIHOU_COMPOSITE_VERSION,
        )
        .order_by(ChihouCalculatedIndex.race_id, ChihouCalculatedIndex.composite_index.desc())
    )
    all_rows = indices_result.all()

    # race_id でグループ化
    indices_map: dict[int, list[Any]] = {}
    seen: set[tuple[int, int]] = set()
    for row in all_rows:
        ci, entry, horse = row
        key = (ci.race_id, ci.horse_id)
        if key in seen:
            continue
        seen.add(key)
        indices_map.setdefault(ci.race_id, []).append((ci, entry, horse))

    # 外部指数コンセンサス取得
    consensus_map = await _fetch_external_consensus(session, race_ids)

    race_data: list[dict[str, Any]] = []
    for race in races:
        rows = indices_map.get(race.id, [])
        if not rows:
            continue

        race_consensus = consensus_map.get(race.id)  # None = 外部データなし

        horses: list[dict[str, Any]] = []
        for ci, entry, horse in rows:
            hn = entry.horse_number
            horses.append({
                "horse_number": hn,
                "horse_name": horse.name,
                "composite_index": round(float(ci.composite_index), 2) if ci.composite_index else None,
                "win_probability": round(float(ci.win_probability), 4) if ci.win_probability else None,
                "place_probability": round(float(ci.place_probability), 4) if ci.place_probability else None,
                # 外部指数コンセンサス: 0〜2（外部データなしの場合は null）
                "external_consensus": race_consensus.get(hn) if race_consensus is not None else None,
            })

        if not horses:
            continue

        ranked = sorted(
            [h for h in horses if h["composite_index"] is not None],
            key=lambda h: h["composite_index"],
            reverse=True,
        )
        gap_1_2 = (
            round(ranked[0]["composite_index"] - ranked[1]["composite_index"], 2)
            if len(ranked) >= 2
            else None
        )

        race_data.append({
            "race_id": race.id,
            "course_name": race.course_name,
            "race_number": race.race_number,
            "race_name": race.race_name,
            "post_time": race.post_time,
            "surface": race.surface,
            "distance": race.distance,
            "head_count": len(horses),
            "gap_1_2": gap_1_2,
            "horses": horses,
        })

    return race_data


async def submit_chihou_recommendations(
    session: AsyncSession,
    date: str,
    items: list[dict[str, Any]],
) -> list[ChihouRaceRecommendation]:
    """Claude定期エージェントが選定した地方推奨をDBに保存する。

    items は以下の形式（件数上限なし）:
        [{"rank": int, "race_id": int, "bet_type": "win"|"place",
          "target_horse_numbers": [int], "reason": str, "confidence": float}, ...]

    既存レコードを削除して上書きする。体言止め変換のみ適用（オッズなしのためEVフィルター不可）。
    """
    race_data = await _collect_chihou_race_data(session, date)
    if not race_data:
        logger.warning("地方推奨提出: %s のレースデータなし", date)
        return []

    race_entry_map = {r["race_id"]: r for r in race_data}

    await session.execute(
        delete(ChihouRaceRecommendation).where(ChihouRaceRecommendation.date == date)
    )

    records: list[ChihouRaceRecommendation] = []
    for item in items:
        race_id = int(item["race_id"])
        race_entry = race_entry_map.get(race_id)
        if race_entry is None:
            logger.warning("地方推奨: race_id=%d が当日データに存在しません", race_id)
            continue

        target_details = []
        for hn in item.get("target_horse_numbers", []):
            horse = next((h for h in race_entry["horses"] if h["horse_number"] == hn), None)
            if horse:
                target_details.append({
                    "horse_number": hn,
                    "horse_name": horse.get("horse_name"),
                    "composite_index": horse.get("composite_index"),
                    "win_probability": horse.get("win_probability"),
                    "place_probability": horse.get("place_probability"),
                })

        rec = ChihouRaceRecommendation(
            date=date,
            rank=int(item["rank"]),
            race_id=race_id,
            bet_type=item["bet_type"],
            target_horses=target_details,
            reason=_to_taigen_dome(item["reason"]),
            confidence=float(item["confidence"]),
        )
        session.add(rec)
        records.append(rec)

    await session.commit()
    logger.info("地方推奨提出完了: %s → %d 件保存（提出: %d件）", date, len(records), len(items))
    return records


async def collect_chihou_recommendation_source(
    session: AsyncSession, date: str
) -> dict[str, Any]:
    """Claude定期エージェントが地方推奨選定に使うソースデータを返す。

    Returns:
        {"date": YYYYMMDD, "races_total": int, "races": [...]}
        races は _collect_chihou_race_data() の出力（指数・外部指数コンセンサス付き、オッズなし）
    """
    race_data = await _collect_chihou_race_data(session, date)
    return {
        "date": date,
        "races_total": len(race_data),
        "races": race_data,
    }


async def update_chihou_results(session: AsyncSession, date: str) -> int:
    """レース結果に基づいて地方推奨の的中・払戻を更新する。"""
    from sqlalchemy.orm.attributes import flag_modified

    result = await session.execute(
        select(ChihouRaceRecommendation).where(ChihouRaceRecommendation.date == date)
    )
    recs = result.scalars().all()
    if not recs:
        return 0

    updated = 0
    for rec in recs:
        target_numbers = [h["horse_number"] for h in rec.target_horses]
        if not target_numbers:
            continue

        any_result = await session.execute(
            select(ChihouRaceResult.id).where(ChihouRaceResult.race_id == rec.race_id).limit(1)
        )
        if not any_result.scalar():
            continue

        results_result = await session.execute(
            select(ChihouRaceResult).where(
                ChihouRaceResult.race_id == rec.race_id,
                ChihouRaceResult.horse_number.in_(target_numbers),
            )
        )
        race_results = results_result.scalars().all()

        correct = False
        payout = None
        finish_map = {r.horse_number: r.finish_position for r in race_results if r.horse_number is not None}

        if rec.bet_type == "win":
            winner = next((r for r in race_results if r.finish_position == 1), None)
            correct = winner is not None
            if correct:
                payout_result = await session.execute(
                    select(ChihouRacePayout).where(
                        ChihouRacePayout.race_id == rec.race_id,
                        ChihouRacePayout.bet_type == "win",
                    )
                )
                payout_rec = payout_result.scalars().first()
                if payout_rec:
                    payout = payout_rec.payout
                elif winner and winner.win_odds is not None:
                    payout = int(float(winner.win_odds) * 100)

        elif rec.bet_type == "place":
            correct = any(r.finish_position is not None and r.finish_position <= 3 for r in race_results)
            if correct:
                place_winner = next(
                    (r for r in race_results if r.finish_position is not None and r.finish_position <= 3),
                    None,
                )
                if place_winner:
                    # race_payouts から複勝払戻を取得
                    place_payout_result = await session.execute(
                        select(ChihouRacePayout).where(
                            ChihouRacePayout.race_id == rec.race_id,
                            ChihouRacePayout.bet_type == "place",
                            ChihouRacePayout.combination == str(place_winner.horse_number),
                        )
                    )
                    place_payout_rec = place_payout_result.scalars().first()
                    if place_payout_rec:
                        payout = place_payout_rec.payout
                    elif place_winner.place_odds is not None:
                        payout = int(float(place_winner.place_odds) * 100)

        updated_horses = [
            {**h, "finish_position": finish_map.get(h.get("horse_number"))}
            for h in rec.target_horses
        ]
        rec.target_horses = updated_horses  # type: ignore[assignment]
        flag_modified(rec, "target_horses")
        rec.result_correct = correct
        rec.result_payout = payout
        rec.result_updated_at = datetime.now(tz=UTC)
        updated += 1

    await session.commit()
    logger.info("地方推奨結果更新: %s → %d 件", date, updated)
    return updated


async def update_chihou_odds_decision(session: AsyncSession) -> int:
    """発走10分前の地方推奨に対してオッズからbuy/pass判断を更新する。

    毎分cronで実行。発走8〜15分前のレースを対象にする。
    """
    from sqlalchemy.orm.attributes import flag_modified

    now = datetime.now(tz=UTC)
    # JST基準での発走時刻から現在時刻を逆算
    # post_time は "HHMM" 形式（JST）
    # 日本時間 = UTC + 9時間
    jst_now = now + timedelta(hours=9)

    # 8〜15分後に発走するレースを対象
    target_times = []
    for delta_min in range(8, 16):
        t = jst_now + timedelta(minutes=delta_min)
        target_times.append(t.strftime("%H%M"))

    today = jst_now.strftime("%Y%m%d")

    recs_result = await session.execute(
        select(ChihouRaceRecommendation)
        .where(
            ChihouRaceRecommendation.date == today,
            ChihouRaceRecommendation.odds_decision.is_(None),
        )
    )
    recs = recs_result.scalars().all()
    if not recs:
        return 0

    rec_race_ids = {rec.race_id for rec in recs}

    # 対象レースの発走時刻確認
    races_result = await session.execute(
        select(ChihouRace).where(ChihouRace.id.in_(list(rec_race_ids)))
    )
    races_map = {r.id: r for r in races_result.scalars().all()}

    target_race_ids = {
        race_id
        for race_id, race in races_map.items()
        if race.post_time and race.post_time in target_times
    }

    if not target_race_ids:
        return 0

    # 最新オッズ取得
    latest_time_result = await session.execute(
        select(func.max(ChihouOddsHistory.fetched_at)).where(
            ChihouOddsHistory.race_id.in_(list(target_race_ids))
        )
    )
    latest_time = latest_time_result.scalar()

    odds_map: dict[int, dict[str, dict[str, float]]] = {}
    if latest_time:
        odds_result = await session.execute(
            select(
                ChihouOddsHistory.race_id,
                ChihouOddsHistory.bet_type,
                ChihouOddsHistory.combination,
                ChihouOddsHistory.odds,
            )
            .where(
                ChihouOddsHistory.race_id.in_(list(target_race_ids)),
                ChihouOddsHistory.bet_type.in_(["win", "place"]),
                ChihouOddsHistory.fetched_at >= latest_time - timedelta(minutes=10),
            )
            .distinct(
                ChihouOddsHistory.race_id,
                ChihouOddsHistory.bet_type,
                ChihouOddsHistory.combination,
            )
            .order_by(
                ChihouOddsHistory.race_id,
                ChihouOddsHistory.bet_type,
                ChihouOddsHistory.combination,
                ChihouOddsHistory.fetched_at.desc(),
            )
        )
        for race_id, bet_type, combination, odds in odds_result:
            if race_id not in odds_map:
                odds_map[race_id] = {"win": {}, "place": {}}
            if odds is not None:
                odds_map[race_id][bet_type][combination] = float(odds)

    updated = 0
    snapshot_at = datetime.now(tz=UTC)

    for rec in recs:
        if rec.race_id not in target_race_ids:
            continue

        target_numbers = [h["horse_number"] for h in rec.target_horses]
        race_odds = odds_map.get(rec.race_id, {})
        win_odds_map = race_odds.get("win", {})
        place_odds_map = race_odds.get("place", {})

        if not win_odds_map and not place_odds_map:
            # オッズ未取得
            rec.odds_decision = None
            rec.odds_decision_reason = "オッズ未取得のため判断保留。"
            rec.odds_decision_at = snapshot_at
        else:
            # スナップショット保存
            rec.snapshot_win_odds = win_odds_map or None
            rec.snapshot_place_odds = place_odds_map or None
            rec.snapshot_at = snapshot_at

            # 推奨馬のEV計算
            best_ev = 0.0
            ev_reason_parts = []
            for hn in target_numbers:
                horse = next((h for h in rec.target_horses if h.get("horse_number") == hn), None)
                if horse is None:
                    continue
                win_prob = horse.get("win_probability") or 0.0
                place_prob = horse.get("place_probability") or 0.0
                hn_str = str(hn)

                if rec.bet_type == "win" and hn_str in win_odds_map:
                    ev = win_prob * win_odds_map[hn_str]
                    best_ev = max(best_ev, ev)
                    ev_reason_parts.append(
                        f"単勝{win_odds_map[hn_str]:.1f}倍×勝率{win_prob*100:.1f}%=EV{ev:.2f}"
                    )
                elif rec.bet_type == "place" and hn_str in place_odds_map:
                    ev = place_prob * place_odds_map[hn_str]
                    best_ev = max(best_ev, ev)
                    ev_reason_parts.append(
                        f"複勝{place_odds_map[hn_str]:.1f}倍×複勝率{place_prob*100:.1f}%=EV{ev:.2f}"
                    )

            if not ev_reason_parts:
                rec.odds_decision = None
                rec.odds_decision_reason = "推奨馬のオッズ未取得のため判断保留。"
            elif best_ev >= _BUY_EV_THRESHOLD:
                rec.odds_decision = "buy"
                rec.odds_decision_reason = f"{'、'.join(ev_reason_parts)}。期待値{best_ev:.2f}で買い推奨。"
            else:
                rec.odds_decision = "pass"
                rec.odds_decision_reason = f"{'、'.join(ev_reason_parts)}。期待値{best_ev:.2f}で見送り推奨。"

            rec.odds_decision_at = snapshot_at

        flag_modified(rec, "snapshot_win_odds")
        flag_modified(rec, "snapshot_place_odds")
        updated += 1

    await session.commit()
    logger.info("地方オッズ判断更新: %d 件", updated)
    return updated


# ---------------------------------------------------------------------------
# スイートスポット自動推奨（v10 win_probability ベース）
# ---------------------------------------------------------------------------

_CHIHOU_V10_VERSION = 10


async def build_chihou_sweet_spot_recommendations(
    session: AsyncSession, date: str
) -> list[dict[str, Any]]:
    """地方競馬スイートスポット自動推奨を生成する（DB保存なし・都度算出）。

    抽出条件: 単勝≥10 ∧ EV 1.0-2.0 ∧ ROI陽性競馬場 ∧ k≤2（混戦除外）。
    v10 LightGBM win_probability バックテスト（2026-01〜04）でROI陽性コースのみ対象。
    """
    # 当日レース取得
    races_result = await session.execute(
        select(ChihouRace)
        .where(ChihouRace.date == date, ChihouRace.course != "83")
        .order_by(ChihouRace.post_time, ChihouRace.id)
    )
    races = races_result.scalars().all()
    if not races:
        return []

    race_ids = [r.id for r in races]

    # v10 指数 + エントリー + 馬名を一括取得
    indices_result = await session.execute(
        select(ChihouCalculatedIndex, ChihouRaceEntry, ChihouHorse)
        .join(
            ChihouRaceEntry,
            (ChihouRaceEntry.race_id == ChihouCalculatedIndex.race_id)
            & (ChihouRaceEntry.horse_id == ChihouCalculatedIndex.horse_id),
        )
        .join(ChihouHorse, ChihouHorse.id == ChihouCalculatedIndex.horse_id)
        .where(
            ChihouCalculatedIndex.race_id.in_(race_ids),
            ChihouCalculatedIndex.version == _CHIHOU_V10_VERSION,
        )
        .order_by(ChihouCalculatedIndex.race_id, ChihouCalculatedIndex.composite_index.desc())
    )
    indices_map: dict[int, list[tuple]] = {}
    seen: set[tuple[int, int]] = set()
    for ci, entry, horse in indices_result.all():
        key = (ci.race_id, ci.horse_id)
        if key in seen:
            continue
        seen.add(key)
        indices_map.setdefault(ci.race_id, []).append((ci, entry, horse))

    # 最新単勝・複勝オッズを1クエリで一括取得（win + place を同時に fetch）
    all_odds_result = await session.execute(
        text("""
            SELECT DISTINCT ON (race_id, bet_type, combination)
                race_id, bet_type, combination, odds
            FROM chihou.odds_history
            WHERE race_id = ANY(:race_ids) AND bet_type IN ('win', 'place')
            ORDER BY race_id, bet_type, combination, fetched_at DESC
        """),
        {"race_ids": race_ids},
    )
    win_odds_by_race: dict[int, dict[str, float]] = {}
    place_odds_by_race: dict[int, dict[str, float]] = {}
    for race_id, bet_type, combo, odds_val in all_odds_result.fetchall():
        if odds_val is None:
            continue
        if bet_type == "win":
            win_odds_by_race.setdefault(race_id, {})[combo] = float(odds_val)
        else:
            place_odds_by_race.setdefault(race_id, {})[combo] = float(odds_val)

    # レース結果（確定後の払戻確認用）
    results_result = await session.execute(
        select(ChihouRaceResult).where(ChihouRaceResult.race_id.in_(race_ids))
    )
    results_map: dict[tuple[int, int], ChihouRaceResult] = {}
    for _rr in results_result.scalars().all():
        if _rr.horse_number is not None:
            results_map[(_rr.race_id, _rr.horse_number)] = _rr

    # 穴軸複勝（upset_place）用: 外部生スコア + リランカー
    upset_reranker = get_chihou_upset_reranker()  # アーティファクト未配置なら None
    ext_raw = await _fetch_external_raw(session, race_ids) if upset_reranker else {}

    now = datetime.now(tz=UTC)
    candidates: list[dict[str, Any]] = []

    def _attach_finish(h: dict[str, Any], race_id: int) -> None:
        rr: ChihouRaceResult | None = results_map.get((race_id, h["horse_number"]))
        h["finish_position"] = rr.finish_position if rr else None
        if rr and rr.win_odds is not None:
            h["win_odds"] = float(rr.win_odds)
        if rr and rr.place_odds is not None:
            h["place_odds"] = float(rr.place_odds)

    for race in races:
        horse_rows = indices_map.get(race.id, [])
        if not horse_rows:
            continue
        win_odds = win_odds_by_race.get(race.id, {})
        place_odds = place_odds_by_race.get(race.id, {})
        if not win_odds:
            continue  # オッズ未取得レースは対象外

        # 1番人気単勝オッズ（place_bet 判定用）
        fav_odds = min(win_odds.values()) if win_odds else None

        # 複勝確率集中度（信頼度算出）
        place_probs = [
            float(ci.place_probability)
            for ci, _, _ in horse_rows
            if ci.place_probability is not None and ci.place_probability > 0
        ]
        race_concentration = calc_race_concentration(place_probs)

        sweet_horses: list[dict[str, Any]] = []
        place_bet_horses: list[dict[str, Any]] = []
        low_trusted: list[dict[str, Any]] = []
        low_untrusted: list[dict[str, Any]] = []
        upset_axis: list[dict[str, Any]] = []

        # 穴軸複勝（upset_place）: 人気薄リランカー スコア算出
        # 2026-06-11 検証 (memory: upset_place_extraction 地方編):
        #   単勝[10,15) × 非オッズスコア上位1/4 × 外部バッジ(吉馬/netkeiba上位3)
        #   test(3分割凍結評価)精度37.5% CI[0.352,0.399] / 発走前-10分 30.7%（市場同数23.3%）
        upset_scores: dict[int, Any] = {}
        if upset_reranker is not None:
            ext_map = ext_raw.get(race.id, {})
            upset_rows = []
            for _ci, _entry, _horse in horse_rows:
                _hn = _entry.horse_number
                if _hn is None:
                    continue
                _kc, _nk = ext_map.get(_hn, (None, None))
                upset_rows.append({
                    "horse_number": _hn,
                    "win_odds": win_odds.get(str(_hn)),
                    "speed_index": float(_ci.speed_index) if _ci.speed_index is not None else None,
                    "last3f_index": float(_ci.last3f_index) if _ci.last3f_index is not None else None,
                    "jockey_index": float(_ci.jockey_index) if _ci.jockey_index is not None else None,
                    "rotation_index": (
                        float(_ci.rotation_index) if _ci.rotation_index is not None else None
                    ),
                    "last_margin_index": (
                        float(_ci.last_margin_index) if _ci.last_margin_index is not None else None
                    ),
                    "kc_sp": _kc,
                    "nk_idx": _nk,
                })
            upset_scores = upset_reranker.score_race(upset_rows, race.head_count)

        # composite_index でレース内順位（降順・同値は先着）。sweet_spot/place_bet の
        # ランキング規則（Phase2）で使用する。
        _ranked = sorted(
            (r for r in horse_rows if r[0].composite_index is not None),
            key=lambda r: float(r[0].composite_index),
            reverse=True,
        )
        rank_by_hn: dict[int, int] = {}
        for _r, (_ci, _entry, _horse) in enumerate(_ranked):
            if _entry.horse_number is not None:
                rank_by_hn[_entry.horse_number] = _r + 1

        for ci, entry, horse in horse_rows:
            hn = entry.horse_number
            wo = win_odds.get(str(hn)) if hn is not None else None
            win_prob = float(ci.win_probability) if ci.win_probability is not None else None
            base = {
                "horse_number": hn,
                "horse_name": horse.name,
                "composite_index": round(float(ci.composite_index), 2) if ci.composite_index else None,
                "win_probability": round(win_prob, 4) if win_prob is not None else None,
                "place_probability": round(float(ci.place_probability), 4) if ci.place_probability else None,
                "win_odds": wo,
                "place_odds": place_odds.get(str(hn)) if hn is not None else None,
                "ev": round(win_prob * wo, 3) if (win_prob and wo) else None,
            }
            idx_rank = rank_by_hn.get(hn) if hn is not None else None
            # 高オッズ穴 / 複穴 は重複可（同一馬が単勝・複勝両方の推奨に出ることを許容）
            if chihou_is_sweet_spot(idx_rank, wo, race.course_name):
                sweet_horses.append({**base})
            if chihou_is_place_bet(idx_rank, wo, fav_odds):
                place_bet_horses.append({**base})
            level = chihou_low_odds_trust_level(wo)
            if level == "trusted":
                low_trusted.append({**base})
            elif level == "untrusted":
                low_untrusted.append({**base})
            us = upset_scores.get(hn) if hn is not None else None
            if us is not None and upset_reranker is not None:
                u_tier = upset_reranker.axis_tier(wo, us["ns"], us["badge_cnt"])
                if u_tier:
                    upset_axis.append(
                        {**base, "_ns": us["ns"], "_tier": u_tier, "_badge": us["badge_cnt"]}
                    )

        # ---- 高オッズ穴狙い（既存）: k≥3 の混戦は除外 ----
        if sweet_horses and len(sweet_horses) < 3:
            for h in sweet_horses:
                _attach_finish(h, race.id)
            max_ev = max((h["ev"] or 0.0) for h in sweet_horses)
            any_finished = any(h["finish_position"] is not None for h in sweet_horses)
            result_correct: bool | None = None
            result_payout: int | None = None
            if any_finished:
                winning = [h for h in sweet_horses if h["finish_position"] == 1]
                if winning:
                    result_correct = True
                    w = winning[0].get("win_odds")
                    result_payout = int(round(float(w) * 100)) if w is not None else None
                else:
                    result_correct = False
                    result_payout = 0

            confidence = 0.65 if max_ev >= 1.8 else (0.60 if max_ev >= 1.4 else 0.55)
            reason = (
                "地方スイートスポット（Phase2）：指数1位 ∧ 単勝10〜30倍 ∧ "
                "割安場（浦和/金沢/高知/笠松/盛岡）。"
                "（クリーンOOS検証 5seed 単勝ROI 1.17） "
                + " / ".join(
                    f"{h['horse_number']}番{h.get('horse_name') or ''}"
                    f"(単{(h.get('win_odds') or 0):.1f}/EV{(h.get('ev') or 0):.2f})"
                    for h in sweet_horses
                )
            )
            candidates.append({
                "race_id": race.id,
                "course_name": race.course_name,
                "race_number": race.race_number,
                "race_name": race.race_name,
                "post_time": race.post_time,
                "surface": race.surface,
                "distance": race.distance,
                "head_count": race.head_count,
                "bet_type": "win",
                "category": "sweet_spot",
                "race_concentration": race_concentration,
                "target_horses": sweet_horses,
                "snapshot_win_odds": {str(k): v for k, v in win_odds.items()},
                "snapshot_place_odds": {str(k): v for k, v in place_odds.items()},
                "snapshot_at": now,
                "reason": reason,
                "confidence": confidence,
                "max_ev": max_ev,
                "result_correct": result_correct,
                "result_payout": result_payout,
                "result_updated_at": now if any_finished else None,
                "created_at": now,
            })

        # ---- 複穴（place_bet）: 断然人気R × 単勝≥10 × 指数3位以内 を複勝買い ----
        # k≥3 の混戦は除外（高オッズ穴と同様）
        if place_bet_horses and len(place_bet_horses) < 3:
            for h in place_bet_horses:
                _attach_finish(h, race.id)
            max_ev = max((h["ev"] or 0.0) for h in place_bet_horses)
            any_finished = any(h["finish_position"] is not None for h in place_bet_horses)
            result_correct = None
            result_payout = None
            if any_finished:
                placed = [
                    h for h in place_bet_horses
                    if h["finish_position"] is not None and h["finish_position"] <= 3
                ]
                if placed:
                    result_correct = True
                    # 払戻は的中馬のうち最も早く確定した馬の place_odds を採用
                    placed.sort(key=lambda x: x["finish_position"])
                    p_odds = placed[0].get("place_odds")
                    result_payout = (
                        int(round(float(p_odds) * 100)) if p_odds is not None else None
                    )
                else:
                    result_correct = False
                    result_payout = 0

            confidence = 0.65 if max_ev >= 1.8 else 0.55
            reason = (
                f"地方複穴（Phase2）：1番人気<{CHIHOU_PLACE_BET_FAV_ODDS_MAX:.1f}倍の断然人気R ∧ "
                f"単勝≥10 ∧ 指数3位以内 の複勝買い。"
                f"（複勝は控除率分マイナス帯 — 予想の参考用） "
                + " / ".join(
                    f"{h['horse_number']}番{h.get('horse_name') or ''}"
                    f"(単{(h.get('win_odds') or 0):.1f}/EV{(h.get('ev') or 0):.2f})"
                    for h in place_bet_horses
                )
            )
            candidates.append({
                "race_id": race.id,
                "course_name": race.course_name,
                "race_number": race.race_number,
                "race_name": race.race_name,
                "post_time": race.post_time,
                "surface": race.surface,
                "distance": race.distance,
                "head_count": race.head_count,
                "bet_type": "place",
                "category": "place_bet",
                "race_concentration": race_concentration,
                "target_horses": place_bet_horses,
                "snapshot_win_odds": {str(k): v for k, v in win_odds.items()},
                "snapshot_place_odds": {str(k): v for k, v in place_odds.items()},
                "snapshot_at": now,
                "reason": reason,
                "confidence": confidence,
                "max_ev": max_ev,
                "result_correct": result_correct,
                "result_payout": result_payout,
                "result_updated_at": now if any_finished else None,
                "created_at": now,
            })

        # ---- 穴軸複勝（upset_place）: 人気薄リランカー軸（2026-06-11 検証） ----
        # レース内は ns 上位2頭まで。的中精度特化（複勝ROI≈0.83 は参考表示）。
        if upset_axis:
            upset_axis.sort(key=lambda h: -h["_ns"])
            upset_picks = upset_axis[:2]
            for h in upset_picks:
                _attach_finish(h, race.id)
            any_finished = any(h["finish_position"] is not None for h in upset_picks)
            result_correct = None
            result_payout = None
            if any_finished:
                placed = [
                    h for h in upset_picks
                    if h["finish_position"] is not None and h["finish_position"] <= 3
                ]
                if placed:
                    result_correct = True
                    placed.sort(key=lambda x: x["finish_position"])
                    p_odds = placed[0].get("place_odds")
                    result_payout = (
                        int(round(float(p_odds) * 100)) if p_odds is not None else None
                    )
                else:
                    result_correct = False
                    result_payout = 0

            max_ns = max(h["_ns"] for h in upset_picks)
            reason = (
                "地方 穴軸複勝（人気薄リランカー）：単勝10-15倍 × 非オッズスコア上位1/4 × "
                "外部バッジ（吉馬/netkeiba上位3）。"
                "検証: 確定オッズ的中37.5%・発走前-10分30.7%（市場同数23.3%比 +7pt）。"
                "複勝ROI≈0.83 — 的中精度特化・予想の参考用。 "
                + " / ".join(
                    f"{h['horse_number']}番{h.get('horse_name') or ''}"
                    f"(単{(h.get('win_odds') or 0):.1f}/バッジ{h['_badge']}"
                    f"{'★' if h['_tier'] == 'strong' else ''})"
                    for h in upset_picks
                )
            )
            candidates.append({
                "race_id": race.id,
                "course_name": race.course_name,
                "race_number": race.race_number,
                "race_name": race.race_name,
                "post_time": race.post_time,
                "surface": race.surface,
                "distance": race.distance,
                "head_count": race.head_count,
                "bet_type": "place",
                "category": "upset_place",
                "race_concentration": race_concentration,
                "target_horses": upset_picks,
                "snapshot_win_odds": {str(k): v for k, v in win_odds.items()},
                "snapshot_place_odds": {str(k): v for k, v in place_odds.items()},
                "snapshot_at": now,
                "reason": reason,
                "confidence": 0.50,
                "max_ev": max_ns,
                "result_correct": result_correct,
                "result_payout": result_payout,
                "result_updated_at": now if any_finished else None,
                "created_at": now,
            })

        # ---- 低オッズ本命（信頼/不信頼）: 各カテゴリ最低オッズ馬1頭のみ採用 ----
        for category, horses, base_reason in (
            (
                "low_odds_trusted",
                low_trusted,
                "信頼できる本命：単勝<1.5（バックテスト的中率約70%）。"
                "ROIは構造的に1.0未満（控除率分の損失帯）— 予想の参考表示。",
            ),
            (
                "low_odds_untrusted",
                low_untrusted,
                "信頼できない本命：1.5≤単勝<2.0（バックテスト的中率約48%）。"
                "ROIは0.81。半分は外れる帯。",
            ),
        ):
            if not horses:
                continue
            horses.sort(key=lambda h: (h.get("win_odds") or 9.99))
            chosen = horses[0]
            _attach_finish(chosen, race.id)
            picked = [chosen]
            any_finished = chosen["finish_position"] is not None
            result_correct = None
            result_payout = None
            if any_finished:
                if chosen["finish_position"] == 1:
                    result_correct = True
                    w = chosen.get("win_odds")
                    result_payout = int(round(float(w) * 100)) if w is not None else None
                else:
                    result_correct = False
                    result_payout = 0
            badge = (
                f"{chosen['horse_number']}番{chosen.get('horse_name') or ''}"
                f"(単{(chosen.get('win_odds') or 0):.1f}"
                f"/v10勝率{((chosen.get('win_probability') or 0) * 100):.0f}%)"
            )
            candidates.append({
                "race_id": race.id,
                "course_name": race.course_name,
                "race_number": race.race_number,
                "race_name": race.race_name,
                "post_time": race.post_time,
                "surface": race.surface,
                "distance": race.distance,
                "head_count": race.head_count,
                "bet_type": "win",
                "category": category,
                "race_concentration": race_concentration,
                "target_horses": picked,
                "snapshot_win_odds": {str(k): v for k, v in win_odds.items()},
                "snapshot_place_odds": {str(k): v for k, v in place_odds.items()},
                "snapshot_at": now,
                "reason": f"{base_reason} {badge}",
                # confidence は信頼=0.7 / 不信頼=0.45 の固定値（バックテストの hit 率に近い値）
                "confidence": 0.70 if category == "low_odds_trusted" else 0.45,
                "max_ev": chosen.get("ev") or 0.0,
                "result_correct": result_correct,
                "result_payout": result_payout,
                "result_updated_at": now if any_finished else None,
                "created_at": now,
            })

    # category × max_ev 降順で rank 付与
    _CATEGORY_ORDER = {
        "sweet_spot": 0,
        "place_bet": 1,
        "upset_place": 2,
        "low_odds_trusted": 3,
        "low_odds_untrusted": 4,
    }
    candidates.sort(key=lambda x: (_CATEGORY_ORDER.get(x.get("category", ""), 99), -x["max_ev"]))
    for i, c in enumerate(candidates, start=1):
        c["rank"] = i
        c["id"] = -c["race_id"] * 10 - _CATEGORY_ORDER.get(c.get("category", ""), 0)
        c.pop("max_ev", None)

    logger.info(
        "地方スイートスポット推奨: %s → 計%d件 (sweet_spot=%d, place_bet=%d, upset=%d, "
        "low_trusted=%d, low_untrusted=%d)",
        date,
        len(candidates),
        sum(1 for c in candidates if c.get("category") == "sweet_spot"),
        sum(1 for c in candidates if c.get("category") == "place_bet"),
        sum(1 for c in candidates if c.get("category") == "upset_place"),
        sum(1 for c in candidates if c.get("category") == "low_odds_trusted"),
        sum(1 for c in candidates if c.get("category") == "low_odds_untrusted"),
    )
    return candidates
