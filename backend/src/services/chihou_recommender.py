"""地方競馬 Claude API推奨生成サービス

オッズなしで指数・信頼度から推奨を生成する。
発走10分前にオッズを取得してbuy/pass判断を更新する。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import anthropic
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
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
from .chihou_recommendation_prompt import CHIHOU_SYSTEM_PROMPT, CHIHOU_USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_CHIHOU_COMPOSITE_VERSION = 2

# buy/pass判断の期待値閾値
_BUY_EV_THRESHOLD = 1.0

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


def _fmt_date(date_str: str) -> str:
    """YYYYMMDD を YYYY-MM-DD 形式に変換する。"""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


async def _fetch_external_consensus(
    session: AsyncSession, race_ids: list[int]
) -> dict[int, dict[int, int]]:
    """sekito.kichiuma / sekito.netkeiba から外部指数コンセンサスを取得する。

    Returns:
        dict[race_id, dict[horse_number, external_consensus]]
        external_consensus: 0〜2（kichiuma/netkeibaのうち何本が1位と一致するか）
        レース自体に外部データが存在しない場合は race_id がキーに含まれない。
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


async def generate_chihou_recommendations(
    session: AsyncSession, date: str
) -> list[ChihouRaceRecommendation]:
    """Claude APIで地方競馬推奨を生成しDBに保存して返す。"""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません")

    race_data = await _collect_chihou_race_data(session, date)
    if not race_data:
        logger.warning("地方推奨生成: %s のレースデータなし", date)
        return []

    races_json = json.dumps(race_data, ensure_ascii=False, indent=2)
    user_prompt = CHIHOU_USER_PROMPT_TEMPLATE.format(date=_fmt_date(date), races_json=races_json)

    logger.info("地方Claude API 推奨生成開始: %s (%d レース)", date, len(race_data))

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        system=CHIHOU_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = next(b.text for b in message.content if hasattr(b, "text")).strip()
    logger.debug("地方Claude APIレスポンス: %s", raw_text[:500])

    try:
        parsed = json.loads(raw_text)
        items: list[dict[str, Any]] = parsed["recommendations"]
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("地方Claude API パース失敗: %s\n%s", e, raw_text)
        raise

    race_entry_map = {r["race_id"]: r for r in race_data}

    await session.execute(
        delete(ChihouRaceRecommendation).where(ChihouRaceRecommendation.date == date)
    )

    records: list[ChihouRaceRecommendation] = []
    for item in items[:5]:
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
    logger.info("地方推奨生成完了: %s → %d 件保存", date, len(records))
    return records


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
