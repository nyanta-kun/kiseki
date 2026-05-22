"""推奨レース・馬券のソース提供 / 提出（Claude定期実行用）

Claude.ai の定期エージェント（Routine）が以下を行う：
1. GET /api/recommendations/source?date=YYYYMMDD で当日の素材データを取得
2. 条件を満たすすべてのレースを推奨として選定（件数上限なし）
3. POST /api/recommendations/submit?date=YYYYMMDD で投入

このサービスは Anthropic API を呼び出さない（API課金なし）。
ハードフィルター・体言止め変換は submit_recommendations() 内で適用する。
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from datetime import date as _date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CalculatedIndex,
    Horse,
    OddsHistory,
    Race,
    RaceEntry,
    RaceRecommendation,
    RaceResult,
)
from ..indices.buy_signal import is_sweet_spot, jra_horse_purchase_signal
from ..indices.composite import COMPOSITE_VERSION

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

        ag_rows = (await session.execute(
            _text("SELECT horse_no, rank FROM sekito.anagusa"
                  " WHERE date = :d AND course_code = :c AND race_no = :r"),
            {"d": race_date, "c": sekito_code, "r": race.race_number},
        )).fetchall()
        anagusa_ranks = {hn: r for hn, r in ag_rows if r in ("A", "B", "C")}

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

        all_horses = (
            set(nb_course.keys()) | set(nb_ave.keys()) | set(km_score.keys())
            | set(anagusa_ranks.keys())
        )
        result[race.id] = {
            hn: {
                "nb_course_rank": nb_course_ranks.get(hn),
                "nb_ave_rank": nb_ave_ranks.get(hn),
                "km_rank": km_ranks.get(hn),
                "anagusa_rank": anagusa_ranks.get(hn),
            }
            for hn in all_horses
        }
    return result

logger = logging.getLogger(__name__)

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
                # JV-Next DM 指数 (DMシグナル算出に使用)
                "jvan_time_dm": _f(entry.jvan_time_dm),
                "jvan_battle_dm": _f(entry.jvan_battle_dm),
                "anagusa_rank": ext.get("anagusa_rank"),
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
        # 上位2頭が3位以下から抜け出している差 (breakaway 指標)
        # v26 検証: top2_t3_gap≥7 で上位2頭中穴オッズ → 単勝ROI 1.593
        top2_t3_gap = (
            round(ranked[1]["composite_index"] - ranked[2]["composite_index"], 2)
            if len(ranked) >= 3
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

            # 購入シグナル (v26 breakaway ROI 検証ベース)
            #   super_buy: 単勝ROI 1.593 (rank<=2 ∧ top2_t3_gap>=7 ∧ オッズ>=10)
            #   buy:       単勝ROI 1.290 (rank<=2 ∧ top2_t3_gap>=5 ∧ オッズ>=10)
            #   watch:     単勝ROI 1.042 (rank<=3 ∧ オッズ>=10)
            h["purchase_signal"] = jra_horse_purchase_signal(
                rank=idx_rank if idx_rank is not None else 99,
                top2_t3_gap=top2_t3_gap if idx_rank is not None and idx_rank <= 2 else None,
                win_odds=h.get("win_odds"),
            )

        # DM シグナルタグをレース全頭に付与（軸/穴/警戒）
        # ベース指数とは独立した「タグ」レイヤ。バックテスト実証 (ROI 最大 188.8%)
        from types import SimpleNamespace

        from ..indices.dm_signals import compute_dm_signals
        sig_objs = [
            SimpleNamespace(
                horse_number=h["horse_number"],
                composite_index=h["composite_index"] or 0.0,
                jvan_time_dm=h.get("jvan_time_dm"),
                jvan_battle_dm=h.get("jvan_battle_dm"),
                anagusa_rank=h.get("anagusa_rank"),
                dm_signals=None,
            )
            for h in horses
        ]
        # 人気は odds_rank_map をそのまま流用 (1=最人気)
        # レース条件を渡して低信頼セグメントは自動除外
        compute_dm_signals(
            sig_objs,
            popularity_map={obj.horse_number: odds_rank_map[obj.horse_number]
                            for obj in sig_objs if obj.horse_number in odds_rank_map},
            win_odds_map={h["horse_number"]: h["win_odds"]
                          for h in horses if h.get("win_odds") is not None},
            course_name=race.course_name,
            surface=race.surface,
            distance=race.distance,
        )
        sig_by_hn = {obj.horse_number: (obj.dm_signals or []) for obj in sig_objs}
        for h in horses:
            h["dm_signals"] = sig_by_hn.get(h["horse_number"], [])

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
            "top2_t3_gap": top2_t3_gap,
            "has_odds": bool(win_odds or place_odds),
            "horses": horses,
        })

    return race_data


async def submit_recommendations(
    session: AsyncSession,
    date: str,
    items: list[dict[str, Any]],
) -> list[RaceRecommendation]:
    """Claude定期エージェントが選定した推奨をDBに保存する。

    items は以下の形式（件数上限なし、ハードフィルター違反は自動除外）:
        [{"rank": 1, "race_id": int, "bet_type": "win"|"place"|"quinella",
          "target_horse_numbers": [int, ...], "reason": str, "confidence": float}, ...]

    保存前にハードフィルター・体言止め変換を適用する。
    既存の推奨がある場合は削除して上書きする。
    """
    race_data = await _collect_race_data(session, date)
    if not race_data:
        logger.warning("推奨提出: %s のレースデータが見つかりません", date)
        return []

    race_data_with_odds = [r for r in race_data if r["has_odds"]]
    if not race_data_with_odds:
        logger.warning("推奨提出: %s のオッズデータが見つかりません", date)
        return []

    # オッズスナップショット（現在時刻）
    snapshot_at = datetime.now(tz=UTC)
    race_entry_map = {r["race_id"]: r for r in race_data_with_odds}

    # 既存レコード削除 → 上書き
    await session.execute(
        delete(RaceRecommendation).where(RaceRecommendation.date == date)
    )

    # ---- ハードフィルター（Claude の指示無視を防ぐバックエンド側の安全網） ----
    # A: index_rank ≤ 3（external_dark_horse=True は例外）
    # B: EV ≥ 1.0（bet_type に応じて ev_win / ev_place を確認、purchase_signal=super_buy/buy は例外）
    # C: win_probability ≥ 0.05（単勝推奨のみ、purchase_signal=super_buy/buy は例外）
    # 例外: purchase_signal が super_buy/buy/watch のいずれかなら EV/勝率下限をスキップ
    #   v26 検証で本シグナルは ROI ≥ 1.042 を実証済み (model EV 計算より信頼度高い)
    def _passes_hard_filter(item: dict[str, Any], race_entry: dict[str, Any], bet_type: str) -> bool:
        for hn in item.get("target_horse_numbers", []):
            horse = next((h for h in race_entry["horses"] if h["horse_number"] == hn), None)
            if horse is None:
                continue
            idx_rank = horse.get("index_rank")
            is_dark = horse.get("external_dark_horse", False)
            purchase_signal = horse.get("purchase_signal")
            has_strong_signal = purchase_signal in ("super_buy", "buy")
            has_any_signal = purchase_signal in ("super_buy", "buy", "watch")
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

            # B: EV下限 (purchase_signal で実証済み高ROI の馬は除外しない)
            ev = ev_win if bet_type == "win" else ev_place
            if ev is not None and ev < 1.0 and not has_any_signal:
                logger.warning(
                    "ハードフィルターB: 馬番%d EV=%.2f < 1.0 → 除外", hn, ev,
                )
                return False

            # C: 勝率下限（単勝のみ、purchase_signal=super_buy/buy 馬は除外しない）
            if bet_type == "win" and not is_dark and not has_strong_signal and win_prob < 0.05:
                logger.warning(
                    "ハードフィルターC: 馬番%d win_prob=%.4f < 0.05 → 除外", hn, win_prob,
                )
                return False

        return True

    records: list[RaceRecommendation] = []
    rank_counter = 1
    for item in items:
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
    logger.info("推奨提出完了: %s → %d 件保存（提出: %d件）", date, len(records), len(items))
    return records


async def collect_recommendation_source(session: AsyncSession, date: str) -> dict[str, Any]:
    """Claude定期エージェントが推奨選定に使うソースデータを返す。

    Returns:
        {"date": YYYYMMDD, "races": [...], "races_with_odds": int}
        races は _collect_race_data() の出力（指数・オッズ・外部指数付き）
    """
    race_data = await _collect_race_data(session, date)
    races_with_odds = [r for r in race_data if r["has_odds"]]
    return {
        "date": date,
        "races_total": len(race_data),
        "races_with_odds": len(races_with_odds),
        "races": races_with_odds,
    }


async def build_sweet_spot_recommendations(
    session: AsyncSession, date: str
) -> list[dict[str, Any]]:
    """スイートスポット自動推奨を生成する（指定日の最新オッズ反映）。

    抽出条件：単勝≥10 ∧ 期待値 1.2-5.0 ∧ バッジあり ∧ レース内 k≤2。
    DB には保存せず、API 呼び出し時に都度算出する（オッズ変動を即時反映するため）。
    3年バックテスト 単ROI 1.188 / 複ROI 0.826 (k≤2 両方買い)。

    Returns:
        recommendations.py の `_to_out` と同じスキーマの dict のリスト。
        rank は EV 降順、各 dict は単一レースの推奨1件（target_horses は複数頭可）。
    """
    race_data = await _collect_race_data(session, date)
    if not race_data:
        return []

    now = datetime.now(UTC)

    # レース結果を一括取得 (確定済みレースの finish_position / 確定オッズ表示用)
    race_ids_all = [r["race_id"] for r in race_data]
    results_map: dict[tuple[int, int], dict[str, Any]] = {}
    if race_ids_all:
        rr_result = await session.execute(
            select(RaceResult).where(RaceResult.race_id.in_(race_ids_all))
        )
        # race_id × horse_id → {finish_position, win_odds, place_odds}
        for rr_obj in rr_result.scalars().all():
            results_map[(rr_obj.race_id, rr_obj.horse_id)] = {
                "finish_position": rr_obj.finish_position,
                "win_odds": float(rr_obj.win_odds) if rr_obj.win_odds is not None else None,
                "place_odds": float(rr_obj.place_odds) if rr_obj.place_odds is not None else None,
            }

    # race_id × horse_number → horse_id の解決マップ (RaceEntry から)
    entry_map: dict[tuple[int, int], int] = {}
    if race_ids_all:
        entries_result = await session.execute(
            select(RaceEntry.race_id, RaceEntry.horse_number, RaceEntry.horse_id)
            .where(RaceEntry.race_id.in_(race_ids_all))
        )
        for race_id, hn, hid in entries_result.all():
            if hn is not None:
                entry_map[(race_id, hn)] = hid

    candidates: list[dict[str, Any]] = []
    for race in race_data:
        if not race["has_odds"]:
            continue
        horses = race["horses"]
        # 各馬の sweet spot 判定
        sweet_horses: list[dict[str, Any]] = []
        for h in horses:
            if is_sweet_spot(
                win_odds=h.get("win_odds"),
                win_probability=h.get("win_probability"),
                composite_rank=h.get("index_rank"),
                dm_signals=h.get("dm_signals"),
                purchase_signal=h.get("purchase_signal"),
                anagusa_rank=h.get("anagusa_rank"),
                nb_course_rank=h.get("nb_course_rank"),
                nb_ave_rank=h.get("nb_ave_rank"),
                km_rank=h.get("km_rank"),
            ):
                sweet_horses.append(h)

        # k≥3 のレースは混戦のため除外（k=3 で単ROI 0.935）
        if not sweet_horses or len(sweet_horses) >= 3:
            continue

        # target_horses 構築 (TargetHorse 互換)
        target_horses: list[dict[str, Any]] = []
        snapshot_win_odds: dict[str, float] = {}
        snapshot_place_odds: dict[str, float] = {}
        max_ev = 0.0
        for h in sweet_horses:
            # レース結果から finish_position / 確定オッズを取得
            horse_id = entry_map.get((race["race_id"], h["horse_number"]))
            rr = results_map.get((race["race_id"], horse_id)) if horse_id else None
            finish_pos = rr["finish_position"] if rr else None
            # 確定オッズ (race_results) があればそちらを優先（odds_history は変動値）
            final_win = rr["win_odds"] if rr and rr.get("win_odds") is not None else h.get("win_odds")
            final_place = rr["place_odds"] if rr and rr.get("place_odds") is not None else h.get("place_odds")
            # 確定オッズで EV を再計算
            ev_win = (
                round(float(h["win_probability"]) * float(final_win), 3)
                if h.get("win_probability") is not None and final_win is not None
                else h.get("ev_win")
            )
            ev_place = (
                round(float(h["place_probability"]) * float(final_place), 3)
                if h.get("place_probability") is not None and final_place is not None
                else h.get("ev_place")
            )

            target_horses.append({
                "horse_number": h["horse_number"],
                "horse_name": h["horse_name"],
                "composite_index": h["composite_index"],
                "win_probability": h["win_probability"],
                "place_probability": h["place_probability"],
                "ev_win": ev_win,
                "ev_place": ev_place,
                "win_odds": final_win,
                "place_odds": final_place,
                "finish_position": finish_pos,
            })
            ev = ev_win or 0.0
            if ev and ev > max_ev:
                max_ev = ev

        for h in horses:
            hn_str = str(h["horse_number"])
            if h.get("win_odds") is not None:
                snapshot_win_odds[hn_str] = float(h["win_odds"])
            if h.get("place_odds") is not None:
                snapshot_place_odds[hn_str] = float(h["place_odds"])

        # reason 構築（バッジ列挙）
        badge_parts: list[str] = []
        for h in sweet_horses:
            tags: list[str] = []
            if h.get("dm_signals"):
                tags.extend(h["dm_signals"])
            if h.get("purchase_signal") == "super_buy":
                tags.append("🔥超推奨")
            elif h.get("purchase_signal") == "buy":
                tags.append("◎推奨")
            elif h.get("purchase_signal") == "watch":
                tags.append("○注目")
            if h.get("anagusa_rank") in ("A", "B", "C"):
                tags.append(f"穴{h['anagusa_rank']}")
            if h.get("external_dark_horse"):
                tags.append("外部穴")
            badge_parts.append(
                f"{h['horse_number']}番{h.get('horse_name') or ''} "
                f"(単{(h.get('win_odds') or 0):.1f}/EV{(h.get('ev_win') or 0):.2f}/{','.join(tags)})"
            )
        reason = (
            "スイートスポット自動推奨：単勝≥10 ∧ 期待値 1.2-5.0 ∧ バッジあり一頭。"
            "（3年実証 単ROI 1.188 / k≤2 両方買い） " + " / ".join(badge_parts)
        )

        # confidence は EV 値ベース (max EV 1.2→0.55, 2.0→0.65, 3.0+→0.72)
        if max_ev >= 3.0:
            confidence = 0.72
        elif max_ev >= 2.0:
            confidence = 0.65
        elif max_ev >= 1.5:
            confidence = 0.60
        else:
            confidence = 0.55

        # 結果集計 (bet_type=win): 対象馬のいずれかが1着なら的中
        # レース未確定 (finish_position が全 None) なら result_correct=None
        any_finished = any(t["finish_position"] is not None for t in target_horses)
        result_correct: bool | None = None
        result_payout: int | None = None
        result_updated_at: datetime | None = None
        if any_finished:
            winning = [t for t in target_horses if t["finish_position"] == 1]
            if winning:
                result_correct = True
                # payout = 単勝オッズ × 100 円ベース (複数頭が1着想定なし)
                w = winning[0].get("win_odds")
                result_payout = int(round(float(w) * 100)) if w is not None else None
            else:
                result_correct = False
                result_payout = 0
            result_updated_at = now

        candidates.append({
            "race_id": race["race_id"],
            "course_name": race["course_name"],
            "race_number": race["race_number"],
            "race_name": race.get("race_name"),
            "post_time": race.get("post_time"),
            "surface": race.get("surface"),
            "distance": race.get("distance"),
            "grade": race.get("grade"),
            "head_count": race.get("head_count"),
            "bet_type": "win",
            "target_horses": target_horses,
            "snapshot_win_odds": snapshot_win_odds,
            "snapshot_place_odds": snapshot_place_odds,
            "snapshot_at": now,
            "reason": reason,
            "confidence": confidence,
            "max_ev": max_ev,
            "result_correct": result_correct,
            "result_payout": result_payout,
            "result_updated_at": result_updated_at,
        })

    # max EV 降順で rank 付け、id は -race_id (DB 主キーと衝突しない負値)
    candidates.sort(key=lambda x: -x["max_ev"])
    for i, c in enumerate(candidates, start=1):
        c["rank"] = i
        c["id"] = -c["race_id"]
        c["created_at"] = now
        c.pop("max_ev", None)

    return candidates


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


# ---------------------------------------------------------------------------
# 穴ぐさルール推奨
# ---------------------------------------------------------------------------

# バックテスト実証済みルール（3年 2023/5/17-2026/5/15, surface='芝'/'ダ' 正確版）
_ANAGUSA_RULES: list[dict[str, Any]] = [
    {
        "label": "Rule1",
        "desc": "東京×芝×1201-1800m",
        "course_name": "東京",
        "surface": "芝",
        "dist_lo": 1201,
        "dist_hi": 1800,
        "bet": "place",  # 複勝
        "backtest_place_roi": 1.044,
        "backtest_win_roi": None,
        "backtest_n": 174,
    },
    {
        "label": "Rule2",
        "desc": "新潟×芝×1601-1800m",
        "course_name": "新潟",
        "surface": "芝",
        "dist_lo": 1601,
        "dist_hi": 1800,
        "bet": "win_place",  # 単+複
        "backtest_place_roi": 1.168,
        "backtest_win_roi": 1.604,
        "backtest_n": 28,
    },
    {
        "label": "Rule3",
        "desc": "京都×芝×~1200m",
        "course_name": "京都",
        "surface": "芝",
        "dist_lo": 0,
        "dist_hi": 1200,
        "bet": "win_place",  # 単+複
        "backtest_place_roi": 1.030,
        "backtest_win_roi": 1.209,
        "backtest_n": 43,
    },
    {
        "label": "Rule4",
        "desc": "京都×ダ×1601-1800m",
        "course_name": "京都",
        "surface": "ダ",
        "dist_lo": 1601,
        "dist_hi": 1800,
        "bet": "win_place",  # 単+複
        "backtest_place_roi": 1.161,
        "backtest_win_roi": 1.472,
        "backtest_n": 150,
    },
]


async def build_anagusa_rule_recommendations(
    session: AsyncSession, date: str
) -> list[dict[str, Any]]:
    """穴ぐさ条件ルールに基づく推奨馬を生成する（オッズ反映・都度算出）。

    抽出条件: sekito.anagusa rank_A × コース/surface/距離ルール。
    人気4-6番が最優先（pop4-6で複ROI 1.192〜1.425の実証値あり）。
    レース結果が確定している場合は finish_position・確定オッズも返す。
    """
    race_data = await _collect_race_data(session, date)
    if not race_data:
        return []

    now = datetime.now(UTC)

    # レース結果を一括取得
    race_ids_all = [r["race_id"] for r in race_data]
    results_map: dict[tuple[int, int], dict[str, Any]] = {}
    entry_map: dict[tuple[int, int], int] = {}
    if race_ids_all:
        rr_result = await session.execute(
            select(RaceResult).where(RaceResult.race_id.in_(race_ids_all))
        )
        for rr_obj in rr_result.scalars().all():
            results_map[(rr_obj.race_id, rr_obj.horse_id)] = {
                "finish_position": rr_obj.finish_position,
                "win_odds": float(rr_obj.win_odds) if rr_obj.win_odds is not None else None,
                "place_odds": float(rr_obj.place_odds) if rr_obj.place_odds is not None else None,
            }
        entries_result = await session.execute(
            select(RaceEntry.race_id, RaceEntry.horse_number, RaceEntry.horse_id)
            .where(RaceEntry.race_id.in_(race_ids_all))
        )
        for race_id, hn, hid in entries_result.all():
            if hn is not None:
                entry_map[(race_id, hn)] = hid

    items: list[dict[str, Any]] = []
    for race in race_data:
        race_surface = (race.get("surface") or "").strip()
        race_dist = race.get("distance") or 0

        for rule in _ANAGUSA_RULES:
            if (
                race["course_name"] != rule["course_name"]
                or race_surface != rule["surface"]
                or not (rule["dist_lo"] <= race_dist <= rule["dist_hi"])
            ):
                continue

            # rank_A 馬を抽出
            a_horses = [h for h in race["horses"] if h.get("anagusa_rank") == "A"]
            if not a_horses:
                break  # このレースに rank_A 馬なし

            for h in a_horses:
                pop = h.get("odds_rank")  # 単勝オッズ順位 = 人気

                # 確定結果取得
                horse_id = entry_map.get((race["race_id"], h["horse_number"]))
                rr = results_map.get((race["race_id"], horse_id)) if horse_id else None
                finish_pos = rr["finish_position"] if rr else None
                final_win = rr["win_odds"] if rr and rr.get("win_odds") is not None else h.get("win_odds")
                final_place = rr["place_odds"] if rr and rr.get("place_odds") is not None else h.get("place_odds")

                items.append({
                    "rule_label": rule["label"],
                    "rule_desc": rule["desc"],
                    "bet_type": rule["bet"],
                    "race_id": race["race_id"],
                    "course_name": race["course_name"],
                    "race_number": race["race_number"],
                    "race_name": race.get("race_name"),
                    "post_time": race.get("post_time"),
                    "distance": race_dist,
                    "surface": race_surface,
                    "horse_number": h["horse_number"],
                    "horse_name": h.get("horse_name"),
                    "win_odds": final_win,
                    "place_odds": final_place,
                    "popularity": pop,
                    "is_preferred_pop": (pop is not None and 4 <= pop <= 6),
                    "finish_position": finish_pos,
                    "backtest_place_roi": rule["backtest_place_roi"],
                    "backtest_win_roi": rule.get("backtest_win_roi"),
                    "backtest_n": rule["backtest_n"],
                    "snapshot_at": now,
                })
            break  # 1レースに対して1ルールのみ適用

    # 発走時刻順・ルール順ソート
    items.sort(key=lambda x: (x["post_time"] is None, x["post_time"] or "", x["rule_label"]))
    return items
