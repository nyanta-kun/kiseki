"""Claude APIを使った推奨レース・馬券生成サービス"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from datetime import date as _date
from typing import Any

import anthropic
from sqlalchemy import delete, func, select
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import CalculatedIndex, Horse, OddsHistory, Race, RaceEntry, RaceRecommendation
from ..indices.composite import COMPOSITE_VERSION
from .recommendation_prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

# JRA 2桁コード → sekito course_code
_JRA_TO_SEKITO: dict[str, str] = {
    "01": "JSPK", "02": "JHKD", "03": "JFKS", "04": "JNGT", "05": "JTOK",
    "06": "JNKY", "07": "JCKO", "08": "JKYO", "09": "JHSN", "10": "JKKR",
}


def _parse_nb_num(raw: str | None) -> float | None:
    """netkeibaの指数文字列から数値を抽出する。"""
    if not raw or raw.strip() in ("-", "", "0"):
        return None
    import re as _re
    m = _re.search(r"\d+", raw)
    return float(m.group()) if m else None


async def _fetch_external_data_batch(
    session: AsyncSession, races: list,
) -> dict[int, dict[int, dict[str, int | None]]]:
    """複数レース分の外部指数ランクを一括取得する。

    Returns:
        {race_id: {horse_no: {nb_course_rank, nb_ave_rank, km_rank}}}
    """
    result: dict[int, dict[int, dict[str, int | None]]] = {}

    for race in races:
        sekito_code = _JRA_TO_SEKITO.get(race.course)
        if not sekito_code:
            continue
        race_date = _date(int(race.date[:4]), int(race.date[4:6]), int(race.date[6:8]))

        nb_rows = (await session.execute(
            _text("SELECT horse_no, idx_course, idx_ave FROM sekito.netkeiba"
                  " WHERE date = :d AND course_code = :c AND race_no = :r"),
            {"d": race_date, "c": sekito_code, "r": race.race_number},
        )).fetchall()

        km_rows = (await session.execute(
            _text("SELECT horse_no, sp_score FROM sekito.kichiuma"
                  " WHERE date = :d AND course_code = :c AND race_no = :r"),
            {"d": race_date, "c": sekito_code, "r": race.race_number},
        )).fetchall()

        nb_course: dict[int, float] = {}
        nb_ave: dict[int, float] = {}
        for horse_no, idx_course, idx_ave in nb_rows:
            c = _parse_nb_num(idx_course)
            a = _parse_nb_num(idx_ave)
            if c is not None:
                nb_course[horse_no] = c
            if a is not None:
                nb_ave[horse_no] = a

        km_score: dict[int, float] = {}
        for horse_no, sp_score in km_rows:
            if sp_score is not None:
                km_score[horse_no] = float(sp_score)

        def _rank(score_map: dict[int, float]) -> dict[int, int]:
            return {hn: i + 1 for i, hn in enumerate(sorted(score_map, key=lambda h: score_map[h], reverse=True))}

        nb_course_ranks = _rank(nb_course)
        nb_ave_ranks = _rank(nb_ave)
        km_ranks = _rank(km_score)

        all_horses = set(nb_course.keys()) | set(nb_ave.keys()) | set(km_score.keys())
        result[race.id] = {
            hn: {
                "nb_course_rank": nb_course_ranks.get(hn),
                "nb_ave_rank": nb_ave_ranks.get(hn),
                "km_rank": km_ranks.get(hn),
            }
            for hn in all_horses
        }
    return result

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
    # 大量の履歴がある日付でもスキャン量を抑えるため、最終取得時刻から60分以内に絞ってから DISTINCT ON する
    race_ids_tuple = tuple(race_ids)

    latest_time_result = await session.execute(
        select(func.max(OddsHistory.fetched_at)).where(
            OddsHistory.race_id.in_(race_ids_tuple)
        )
    )
    latest_time = latest_time_result.scalar()
    time_filter = (
        [OddsHistory.fetched_at >= latest_time - timedelta(minutes=5)]
        if latest_time is not None
        else []
    )

    odds_result = await session.execute(
        select(
            OddsHistory.race_id,
            OddsHistory.bet_type,
            OddsHistory.combination,
            OddsHistory.odds,
        )
        .where(
            OddsHistory.race_id.in_(race_ids_tuple),
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
    all_odds = odds_result.all()

    # オッズを race_id → {win: {馬番str: 倍率}, place: {馬番str: 倍率}} に整理
    odds_map: dict[int, dict[str, dict[str, float]]] = {}
    for race_id, bet_type, combination, odds in all_odds:
        if race_id not in odds_map:
            odds_map[race_id] = {"win": {}, "place": {}}  # type: ignore[index]
        if odds is not None:
            odds_map[race_id][bet_type][combination] = float(odds)  # type: ignore[index]

    # 外部指数ランクを一括取得（sekito.netkeiba / sekito.kichiuma）
    external_data = await _fetch_external_data_batch(session, list(races))

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

        race_ext = external_data.get(race.id, {})

        horses: list[dict[str, Any]] = []
        for ci, entry, horse in rows:
            hn_str = str(entry.horse_number)
            w_odds = win_odds.get(hn_str)
            p_odds = place_odds.get(hn_str)
            win_prob = _f(ci.win_probability)
            place_prob = _f(ci.place_probability)
            ev_win = round(win_prob * w_odds, 3) if win_prob is not None and w_odds is not None else None
            ev_place = round(place_prob * p_odds, 3) if place_prob is not None and p_odds is not None else None

            ext = race_ext.get(entry.horse_number, {})
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
                "nb_course_rank": ext.get("nb_course_rank"),
                "nb_ave_rank": ext.get("nb_ave_rank"),
                "km_rank": ext.get("km_rank"),
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

        # 外部指数穴馬候補を計算（CI4位以下でnb_course_rank=1、またはNB1×KM1）
        ci_rank_map = {h["horse_number"]: i + 1 for i, h in enumerate(ranked)}

        # オッズ人気順（単勝オッズ昇順 = 1位が最も人気）
        win_odds_sorted = sorted(
            [h for h in horses if h.get("win_odds") is not None],
            key=lambda h: h["win_odds"],
        )
        odds_rank_map = {h["horse_number"]: i + 1 for i, h in enumerate(win_odds_sorted)}

        for h in horses:
            ci_rank = ci_rank_map.get(h["horse_number"], 99)
            nb_cr = h.get("nb_course_rank")
            nb_ar = h.get("nb_ave_rank")
            km_r = h.get("km_rank")
            h["external_dark_horse"] = (
                ci_rank >= 4
                and (
                    nb_cr == 1  # コース指数1位（最も有効なシグナル）
                    or (nb_ar is not None and nb_ar <= 2 and km_r == 1)  # NB上位2×KM1位
                )
            )

            # 指数順位・オッズ人気順・乖離を付与
            # odds_rank_gap > 0: 指数より人気がない（大衆が過小評価）
            # odds_rank_gap < 0: 指数より人気がある（大衆が過大評価）
            idx_rank = ci_rank_map.get(h["horse_number"])
            odds_rank = odds_rank_map.get(h["horse_number"])
            h["index_rank"] = idx_rank
            h["odds_rank"] = odds_rank
            h["odds_rank_gap"] = (
                odds_rank - idx_rank
                if idx_rank is not None and odds_rank is not None
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

    # マークダウンコードブロック除去 → JSONブロック抽出
    json_text = re.sub(r"```(?:json)?\s*", "", raw_text).strip()
    json_match = re.search(r"\{[\s\S]*\}", json_text)
    if json_match:
        json_text = json_match.group(0)

    try:
        parsed = json.loads(json_text)
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

    # ---- ハードフィルター（Claude の指示無視を防ぐバックエンド側の安全網） ----
    # A: index_rank ≤ 3（external_dark_horse=True は例外）
    # B: EV ≥ 1.0（bet_type に応じて ev_win / ev_place を確認）
    # C: win_probability ≥ 0.05（単勝推奨のみ）
    def _passes_hard_filter(item: dict[str, Any], race_entry: dict[str, Any], bet_type: str) -> bool:
        for hn in item.get("target_horse_numbers", []):
            horse = next((h for h in race_entry["horses"] if h["horse_number"] == hn), None)
            if horse is None:
                continue
            idx_rank = horse.get("index_rank")
            is_dark = horse.get("external_dark_horse", False)
            ev_win = horse.get("ev_win")
            ev_place = horse.get("ev_place")
            win_prob = horse.get("win_probability") or 0.0

            # A: 指数ランク制約
            if idx_rank is not None and idx_rank > 3 and not is_dark:
                logger.warning(
                    "ハードフィルターA: 馬番%d 指数%d位（external_dark_horse=False）→ 除外",
                    hn, idx_rank,
                )
                return False

            # B: EV下限
            ev = ev_win if bet_type == "win" else ev_place
            if ev is not None and ev < 1.0:
                logger.warning(
                    "ハードフィルターB: 馬番%d EV=%.2f < 1.0 → 除外", hn, ev,
                )
                return False

            # C: 勝率下限（単勝のみ）
            if bet_type == "win" and not is_dark and win_prob < 0.05:
                logger.warning(
                    "ハードフィルターC: 馬番%d win_prob=%.4f < 0.05 → 除外", hn, win_prob,
                )
                return False

        return True

    records: list[RaceRecommendation] = []
    rank_counter = 1
    for item in items[:5]:
        race_id = int(item["race_id"])
        race_entry = race_entry_map.get(race_id)
        if race_entry is None:
            logger.warning("推奨: race_id=%d が当日データに存在しません", race_id)
            continue

        bet_type = item.get("bet_type", "win")

        # ハードフィルター適用
        if not _passes_hard_filter(item, race_entry, bet_type):
            logger.info("推奨スキップ（ハードフィルター）: race_id=%d", race_id)
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
                    "index_rank": horse.get("index_rank"),
                })

        rec = RaceRecommendation(
            date=date,
            rank=rank_counter,  # フィルター後の連番で再採番
            race_id=race_id,
            bet_type=bet_type,
            target_horses=target_details,
            snapshot_win_odds=win_snap or None,
            snapshot_place_odds=place_snap or None,
            snapshot_at=snapshot_at,
            reason=_to_taigen_dome(item["reason"]),
            confidence=float(item["confidence"]),
        )
        session.add(rec)
        records.append(rec)
        rank_counter += 1

    await session.commit()
    logger.info("推奨生成完了: %s → %d 件保存（フィルター前: %d件）", date, len(records), len(items))
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

        # レース全体の成績が取込済みかチェック（対象馬の有無に関わらず）
        any_result = await session.execute(
            select(RaceResult.id).where(RaceResult.race_id == rec.race_id).limit(1)
        )
        if not any_result.scalar():
            continue  # 成績未確定（まだ取り込まれていない）

        # 推奨馬の結果を取得（取消等で存在しない場合もある）
        results_result = await session.execute(
            select(RaceResult).where(
                RaceResult.race_id == rec.race_id,
                RaceResult.horse_number.in_(target_numbers),
            )
        )
        race_results = results_result.scalars().all()

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
