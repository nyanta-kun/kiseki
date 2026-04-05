"""Claude APIを使った推奨レース・馬券生成サービス"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

import anthropic
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import CalculatedIndex, Horse, OddsHistory, Race, RaceEntry, RaceRecommendation
from ..indices.composite import COMPOSITE_VERSION
from .recommendation_prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

# 文末の断定形を体言止めに変換するパターン
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
    """推奨理由の各文末の「〜だ。」を体言止めに変換する。"""
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
    """YYYYMMDD → YYYY-MM-DD"""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"


async def _collect_race_data(session: AsyncSession, date: str) -> list[dict[str, Any]]:
    """当日の全レースデータ（指数・オッズ付き）を収集する。"""
    races_result = await session.execute(
        select(Race).where(Race.date == date).order_by(Race.post_time, Race.id)
    )
    races = races_result.scalars().all()
    if not races:
        return []

    race_ids = [r.id for r in races]

    # 指数 + RaceEntry + Horse のjoin（horse_number / horse_name を取得）
    indices_result = await session.execute(
        select(CalculatedIndex, RaceEntry, Horse)
        .join(
            RaceEntry,
            (RaceEntry.race_id == CalculatedIndex.race_id)
            & (RaceEntry.horse_id == CalculatedIndex.horse_id),
        )
        .join(Horse, Horse.id == CalculatedIndex.horse_id)
        .where(
            CalculatedIndex.race_id.in_(race_ids),
            CalculatedIndex.version == COMPOSITE_VERSION,
        )
        .order_by(CalculatedIndex.race_id, CalculatedIndex.composite_index.desc())
    )
    all_rows = indices_result.all()

    # オッズ（win/place）の最新値を取得
    odds_result = await session.execute(
        select(OddsHistory)
        .where(
            OddsHistory.race_id.in_(race_ids),
            OddsHistory.bet_type.in_(["win", "place"]),
        )
        .order_by(OddsHistory.race_id, OddsHistory.bet_type, OddsHistory.fetched_at.desc())
    )
    all_odds = odds_result.scalars().all()

    # オッズを race_id → {win: {馬番str: 倍率}, place: {馬番str: 倍率}} に整理（最新のみ）
    odds_map: dict[int, dict[str, dict[str, float]]] = {}
    seen_odds: set[tuple[int | None, str, str]] = set()
    for o in all_odds:
        odds_key = (o.race_id, o.bet_type, o.combination)
        if odds_key in seen_odds:
            continue
        seen_odds.add(odds_key)
        if o.race_id not in odds_map:
            odds_map[o.race_id] = {"win": {}, "place": {}}  # type: ignore[index]
        if o.odds is not None:
            odds_map[o.race_id][o.bet_type][o.combination] = float(o.odds)  # type: ignore[index]

    # 指数を race_id でグループ化（horse_id の重複排除）
    indices_map: dict[int, list[tuple[CalculatedIndex, RaceEntry, Horse]]] = {}
    seen_horse: set[tuple[int | None, int | None]] = set()
    for row in all_rows:
        ci, entry, horse = row
        horse_key = (ci.race_id, ci.horse_id)
        if horse_key in seen_horse:
            continue
        seen_horse.add(horse_key)
        indices_map.setdefault(ci.race_id, []).append((ci, entry, horse))

    def _f(v: Any) -> float | None:
        return float(v) if v is not None else None

    race_data: list[dict[str, Any]] = []
    for race in races:
        rows = indices_map.get(race.id, [])
        if not rows:
            continue

        win_odds = odds_map.get(race.id, {}).get("win", {})
        place_odds = odds_map.get(race.id, {}).get("place", {})

        horses: list[dict[str, Any]] = []
        for ci, entry, horse in rows:
            hn_str = str(entry.horse_number)
            w_odds = win_odds.get(hn_str)
            p_odds = place_odds.get(hn_str)
            win_prob = _f(ci.win_probability)
            place_prob = _f(ci.place_probability)
            ev_win = round(win_prob * w_odds, 3) if win_prob is not None and w_odds is not None else None
            ev_place = round(place_prob * p_odds, 3) if place_prob is not None and p_odds is not None else None

            horses.append({
                "horse_number": entry.horse_number,
                "horse_name": horse.name,
                "composite_index": round(float(ci.composite_index), 2) if ci.composite_index else None,
                "win_probability": round(win_prob, 4) if win_prob is not None else None,
                "place_probability": round(place_prob, 4) if place_prob is not None else None,
                "win_odds": w_odds,
                "place_odds": p_odds,
                "ev_win": ev_win,
                "ev_place": ev_place,
                "anagusa_index": round(float(ci.anagusa_index), 1) if ci.anagusa_index else None,
            })

        if not horses:
            continue

        # 指数1位・2位の差
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
            "grade": race.grade,
            "head_count": race.head_count,
            "gap_1_2": gap_1_2,
            "has_odds": bool(win_odds or place_odds),
            "horses": horses,
        })

    return race_data


async def generate_recommendations(session: AsyncSession, date: str) -> list[RaceRecommendation]:
    """Claude APIを呼び出して推奨を生成し、DBに保存して返す。

    既存の推奨がある場合は削除して再生成する。
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません")

    race_data = await _collect_race_data(session, date)
    if not race_data:
        logger.warning("推奨生成: %s のレースデータが見つかりません", date)
        return []

    race_data_with_odds = [r for r in race_data if r["has_odds"]]
    if not race_data_with_odds:
        logger.warning("推奨生成: %s のオッズデータが見つかりません", date)
        return []

    races_json = json.dumps(race_data_with_odds, ensure_ascii=False, indent=2)
    user_prompt = USER_PROMPT_TEMPLATE.format(date=_fmt_date(date), races_json=races_json)

    logger.info("Claude API 推奨生成開始: %s (%d レース)", date, len(race_data_with_odds))

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = next(b.text for b in message.content if hasattr(b, "text")).strip()
    logger.debug("Claude API レスポンス: %s", raw_text[:500])

    try:
        parsed = json.loads(raw_text)
        items: list[dict[str, Any]] = parsed["recommendations"]
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Claude API レスポンスのパース失敗: %s\n%s", e, raw_text)
        raise

    # オッズスナップショット（現在時刻）
    snapshot_at = datetime.now(tz=UTC)
    race_entry_map = {r["race_id"]: r for r in race_data_with_odds}

    # 既存レコード削除 → 再生成
    await session.execute(
        delete(RaceRecommendation).where(RaceRecommendation.date == date)
    )

    records: list[RaceRecommendation] = []
    for item in items[:5]:
        race_id = int(item["race_id"])
        race_entry = race_entry_map.get(race_id)
        if race_entry is None:
            logger.warning("推奨: race_id=%d が当日データに存在しません", race_id)
            continue

        win_snap = {str(h["horse_number"]): h["win_odds"] for h in race_entry["horses"] if h["win_odds"] is not None}
        place_snap = {str(h["horse_number"]): h["place_odds"] for h in race_entry["horses"] if h["place_odds"] is not None}

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
                    "ev_win": horse.get("ev_win"),
                    "ev_place": horse.get("ev_place"),
                    "win_odds": horse.get("win_odds"),
                    "place_odds": horse.get("place_odds"),
                })

        rec = RaceRecommendation(
            date=date,
            rank=int(item["rank"]),
            race_id=race_id,
            bet_type=item["bet_type"],
            target_horses=target_details,
            snapshot_win_odds=win_snap or None,
            snapshot_place_odds=place_snap or None,
            snapshot_at=snapshot_at,
            reason=_to_taigen_dome(item["reason"]),
            confidence=float(item["confidence"]),
        )
        session.add(rec)
        records.append(rec)

    await session.commit()
    logger.info("推奨生成完了: %s → %d 件保存", date, len(records))
    return records


async def update_results(session: AsyncSession, date: str) -> int:
    """レース結果に基づいて推奨の的中・払戻を更新する。

    Returns:
        更新件数
    """
    from ..db.models import RacePayout, RaceResult

    result = await session.execute(
        select(RaceRecommendation).where(RaceRecommendation.date == date)
    )
    recs = result.scalars().all()
    if not recs:
        return 0

    updated = 0
    for rec in recs:
        target_numbers = [h["horse_number"] for h in rec.target_horses]
        if not target_numbers:
            continue

        results_result = await session.execute(
            select(RaceResult).where(
                RaceResult.race_id == rec.race_id,
                RaceResult.horse_number.in_(target_numbers),
            )
        )
        race_results = results_result.scalars().all()
        if not race_results:
            continue

        correct = False
        payout = None

        # 着順マップ {horse_number: finish_position}
        finish_map = {r.horse_number: r.finish_position for r in race_results if r.horse_number is not None}

        if rec.bet_type == "win":
            winner = next((r for r in race_results if r.finish_position == 1), None)
            correct = winner is not None
            if correct:
                # race_payouts から単勝払戻を優先取得、なければ win_odds × 100 で代用
                payout_result = await session.execute(
                    select(RacePayout).where(
                        RacePayout.race_id == rec.race_id,
                        RacePayout.bet_type == "win",
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
                if place_winner and place_winner.place_odds is not None:
                    payout = int(float(place_winner.place_odds) * 100)

        # target_horses に着順を追記（JSONB更新）
        # SQLAlchemy は JSONB 内部変更を自動検知しないため flag_modified が必要
        from sqlalchemy.orm.attributes import flag_modified

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
    logger.info("推奨結果更新: %s → %d 件", date, updated)
    return updated
