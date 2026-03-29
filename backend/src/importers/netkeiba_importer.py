"""netkeibaスクレイピングデータのDBインポーター

処理フロー:
  1. 枠順確定済みレースIDリストを受け取る（1日分まとめて渡す）
  2. 全レース横断で出走馬の前走 jravan_race_id を収集
  3. ユニークな jravan_race_id に集約（重複排除・取得済みスキップ）
  4. 各ユニーク前走レースを1回だけスクレイピング（3〜5秒ウェイト）
  5. (race_id, horse_id) にマッピングして netkeiba_race_extras へ UPSERT
  6. 備考から不利フラグを判定し calculated_indices.disadvantage_flag を更新

IP制限対策: 429/403 受信時は即時停止してエラーを上位に伝播。
"""

import logging
import os
import time

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .netkeiba_scraper import _wait, create_session, scrape_race

# バックフィル時の開催日間スリープ秒数。
# 1日分のデータを一括コミット後に待機し、VPS PostgreSQL の I/O 負荷を分散する。
_DATE_INTERVAL_SECONDS = 30

logger = logging.getLogger(__name__)

# 「被害を受けた馬」を示すキーワード（加害側・完走不可は含めない）
#
# 含める:
#   出遅れ         → スタートが遅れた（常に被害側）
#   不利           → 他馬から妨害を受けた（S不利・直線不利・向正面不利等を含む）
#   S接触          → スタート時に他馬から接触を受けた（被害側）
#                    ※ 単純な「接触」は加害馬の行為を示す場合があるため除外
#   内に張られ等   → 他馬に押された被害表現
#
# 除外:
#   接触（単独）   → 加害馬の行為を示すことが多い（S接触は除く）
#   斜行           → 加害馬の行為を示す表現
#   競走除外・競走中止 → 完走できなかった事象（能力評価の文脈では不利でない）
#   落馬           → 次走で巻き返し推定の根拠にはならない
_DISADVANTAGE_KEYWORDS = frozenset([
    "出遅れ",
    "不利",
    "S接触",
    "内に張られ",
    "外に張られ",
    "内に寄られ",
    "外に寄られ",
    "弾かれ",
    "挟まれ",
])


def _is_disadvantage(remarks: str | None) -> bool:
    if not remarks:
        return False
    return any(kw in remarks for kw in _DISADVANTAGE_KEYWORDS)


def import_previous_race_extras(
    session: Session,
    target_race_ids: int | list[int],
) -> int:
    """枠順確定済みレースの前走備考をスクレイピングしてDBに格納する。

    複数レースをまとめて渡すことで、前走が同一レースの馬を重複スクレイピングせず
    1 jravan_race_id につき 1 リクエストに集約する。

    Args:
        session: SQLAlchemy セッション
        target_race_ids: 枠順確定した races.id（単数または複数）

    Returns:
        DB格納した (race_id, horse_id) ペア数
    """
    from ..db.models import (
        CalculatedIndex,
        Horse,
        NetkeibaRaceExtra,
        Race,
        RaceEntry,
        RaceResult,
    )

    if isinstance(target_race_ids, int):
        target_race_ids = [target_race_ids]

    user_id = os.environ.get("NETKEIBA_USER_ID", "")
    password = os.environ.get("NETKEIBA_PASSWORD", "")
    if not user_id or not password:
        raise RuntimeError("NETKEIBA_USER_ID / NETKEIBA_PASSWORD が未設定です")

    # ── Step 1: 全対象レースの出走馬を一括取得 ──────────────────────────────
    all_entries: list[tuple[int, int]] = session.execute(  # (horse_id, target_race_id)
        select(RaceEntry.horse_id, RaceEntry.race_id)
        .where(RaceEntry.race_id.in_(target_race_ids))
    ).all()

    if not all_entries:
        logger.warning("対象レース %s に出走馬が見つかりません", target_race_ids)
        return 0

    logger.info(
        "対象レース %d 本、合計 %d 頭分の前走を収集します",
        len(target_race_ids), len(all_entries),
    )

    # ── Step 2: 各馬の前走レースを取得 ───────────────────────────────────────
    # jravan_race_id → {(horse_id, prev_race_db_id), ...}
    # 同一前走レースに複数 (horse_id, target_race) が属いても1エントリで管理
    scrape_map: dict[str, set[tuple[int, int]]] = {}

    for horse_id, target_race_id in all_entries:
        prev = session.execute(
            select(RaceResult.race_id, Race.jravan_race_id)
            .join(Race, Race.id == RaceResult.race_id)
            .where(RaceResult.horse_id == horse_id)
            .where(RaceResult.race_id.not_in(target_race_ids))
            .order_by(Race.date.desc(), Race.post_time.desc())
            .limit(1)
        ).first()

        if prev is None or prev.jravan_race_id is None:
            logger.debug("馬ID %d: 前走なし", horse_id)
            continue

        scrape_map.setdefault(prev.jravan_race_id, set()).add(
            (horse_id, prev.race_id)
        )

    if not scrape_map:
        logger.warning("スクレイピング対象の前走が見つかりません")
        return 0

    # ── Step 3: 取得済みをスキップ ───────────────────────────────────────────
    all_prev_race_ids = {race_db_id for pairs in scrape_map.values() for _, race_db_id in pairs}
    existing: set[tuple[int, int]] = set(
        session.execute(
            select(NetkeibaRaceExtra.race_id, NetkeibaRaceExtra.horse_id)
            .where(NetkeibaRaceExtra.race_id.in_(all_prev_race_ids))
        ).all()
    )

    # 取得済みペアを除外し、スクレイピングが不要になった jravan_race_id も除去
    filtered_scrape_map: dict[str, set[tuple[int, int]]] = {}
    skip_count = 0
    for jv_id, pairs in scrape_map.items():
        new_pairs = {(h, r) for h, r in pairs if (r, h) not in existing}
        if new_pairs:
            filtered_scrape_map[jv_id] = new_pairs
        else:
            skip_count += len(pairs)

    if skip_count:
        logger.info("取得済みのためスキップ: %d ペア", skip_count)

    if not filtered_scrape_map:
        logger.info("全馬の前走が取得済みです")
        return 0

    logger.info(
        "スクレイピング対象: %d ユニーク前走レース（%d ペア）",
        len(filtered_scrape_map),
        sum(len(v) for v in filtered_scrape_map.values()),
    )

    # ── Step 4: ユニーク前走レースを1回ずつスクレイピング ─────────────────────
    client = create_session(user_id, password)
    stored_count = 0

    try:
        for jravan_race_id, horse_pairs in filtered_scrape_map.items():
            try:
                data = scrape_race(client, jravan_race_id)
            except httpx.HTTPStatusError as e:
                logger.error("スクレイピング停止（レート制限）: %s", e)
                raise

            race_analysis = data["race_analysis"]
            notable_map: dict[str, str] = data.get("notable_comments", {})
            remarks_map: dict[str, str | None] = {
                h["horse_name"]: h["remarks"] for h in data["horses"]
            }

            # ── Step 5: (horse_id, prev_race_db_id) ペアごとにDB格納 ──────────
            for horse_id, prev_race_db_id in horse_pairs:
                horse_row = session.get(Horse, horse_id)
                horse_name = horse_row.name if horse_row else None

                remarks = remarks_map.get(horse_name) if horse_name else None
                notable = notable_map.get(horse_name) if horse_name else None

                session.execute(
                    insert(NetkeibaRaceExtra).values(
                        race_id=prev_race_db_id,
                        horse_id=horse_id,
                        remarks=remarks,
                        notable_comment=notable,
                        race_analysis=race_analysis,
                    ).on_conflict_do_update(
                        constraint="uq_netkeiba_race_extras_race_horse",
                        set_={
                            "remarks": remarks,
                            "notable_comment": notable,
                            "race_analysis": race_analysis,
                            "scraped_at": "now()",
                        },
                    )
                )

                # ── Step 6: 不利フラグ更新 ──────────────────────────────────
                if _is_disadvantage(remarks):
                    session.execute(
                        update(CalculatedIndex)
                        .where(CalculatedIndex.race_id == prev_race_db_id)
                        .where(CalculatedIndex.horse_id == horse_id)
                        .values(disadvantage_flag=True)
                    )
                    logger.info(
                        "不利フラグ ON: race=%d horse=%d remarks=%r",
                        prev_race_db_id, horse_id, remarks,
                    )

                stored_count += 1

            session.commit()

    finally:
        client.close()

    logger.info("完了: %d ペアをDB格納", stored_count)
    return stored_count


def import_for_date(session: Session, date: str) -> int:
    """指定日の全レースを対象に前走備考を一括インポートする。

    Args:
        session: SQLAlchemy セッション
        date: 開催日 (YYYYMMDD)

    Returns:
        DB格納した (race_id, horse_id) ペア数
    """
    from ..db.models import Race, RaceEntry

    race_ids: list[int] = session.execute(
        select(Race.id)
        .where(Race.date == date)
        .where(
            Race.id.in_(select(RaceEntry.race_id).distinct())
        )
    ).scalars().all()

    if not race_ids:
        logger.warning("date=%s に出走馬のいるレースが見つかりません", date)
        return 0

    logger.info("date=%s: %d レースを対象にインポート開始", date, len(race_ids))
    return import_previous_race_extras(session, race_ids)


def import_race_remarks_direct(
    session: Session,
    date: str,
    client: "httpx.Client | None" = None,
) -> int:
    """指定日のレース本体の備考を直接スクレイピングしてDBに格納する。

    import_for_date とは異なり「前走」ではなく「当該レース自体」のデータを収集する。
    過去データの遡り収集（バックフィル）専用。

    複数日をまとめて処理する場合は client を外部から渡すことでセッションを使い回せる。
    client を省略した場合は環境変数からログインして自動作成・自動クローズする。

    Args:
        session: SQLAlchemy セッション
        date: 開催日 (YYYYMMDD)
        client: ログイン済み httpx.Client（省略時は自動作成）

    Returns:
        DB格納した (race_id, horse_id) ペア数
    """
    from ..db.models import (
        CalculatedIndex,
        Horse,
        NetkeibaRaceExtra,
        Race,
        RaceEntry,
    )

    # ── Step 1: 当日レース一覧を取得 ──────────────────────────────────────────
    races: list[tuple[int, str]] = session.execute(
        select(Race.id, Race.jravan_race_id)
        .where(Race.date == date)
        .where(Race.jravan_race_id.is_not(None))
        .order_by(Race.id)
    ).all()

    if not races:
        logger.warning("date=%s にレースが見つかりません", date)
        return 0

    race_ids = [r[0] for r in races]

    # ── Step 2: 取得済み (race_id, horse_id) を確認 ───────────────────────────
    existing: set[tuple[int, int]] = set(
        session.execute(
            select(NetkeibaRaceExtra.race_id, NetkeibaRaceExtra.horse_id)
            .where(NetkeibaRaceExtra.race_id.in_(race_ids))
        ).all()
    )

    # ── Step 3: 各レースの出走馬を一括取得 ───────────────────────────────────
    entries = session.execute(
        select(RaceEntry.race_id, RaceEntry.horse_id)
        .where(RaceEntry.race_id.in_(race_ids))
    ).all()

    race_horse_map: dict[int, set[int]] = {}
    for e in entries:
        race_horse_map.setdefault(e.race_id, set()).add(e.horse_id)

    all_horse_ids = {h for s in race_horse_map.values() for h in s}
    if not all_horse_ids:
        logger.warning("date=%s に出走馬が見つかりません", date)
        return 0

    # ── Step 4: horse_id → 馬名 マップ ───────────────────────────────────────
    horse_name_map: dict[int, str] = {
        row.id: row.name
        for row in session.execute(
            select(Horse.id, Horse.name).where(Horse.id.in_(all_horse_ids))
        ).all()
    }

    # ── Step 5: スクレイピング ────────────────────────────────────────────────
    own_client = client is None
    if own_client:
        user_id = os.environ.get("NETKEIBA_USER_ID", "")
        password = os.environ.get("NETKEIBA_PASSWORD", "")
        if not user_id or not password:
            raise RuntimeError("NETKEIBA_USER_ID / NETKEIBA_PASSWORD が未設定です")
        client = create_session(user_id, password)

    stored_count = 0
    # 1日分の書き込みをすべてここに溜め、最後に一括コミットする
    disadvantage_updates: list[tuple[int, int]] = []  # (race_id, horse_id)

    try:
        for race_id, jravan_race_id in races:
            horse_ids_in_race = race_horse_map.get(race_id, set())
            if not horse_ids_in_race:
                continue

            # 全馬取得済みならスキップ
            pending = {h for h in horse_ids_in_race if (race_id, h) not in existing}
            if not pending:
                logger.debug("race_id=%d: 全馬取得済みスキップ", race_id)
                continue

            try:
                data = scrape_race(client, jravan_race_id)
            except httpx.HTTPStatusError as e:
                logger.error("スクレイピング停止（レート制限）: %s", e)
                raise

            _wait()  # HTTP レート制限対策（3〜5秒）

            remarks_map: dict[str, str | None] = {
                h["horse_name"]: h["remarks"] for h in data["horses"]
            }

            for horse_id in pending:
                horse_name = horse_name_map.get(horse_id)
                # remarks のみ保存（race_analysis/notable_comment はレース内重複のため省略）
                remarks = remarks_map.get(horse_name) if horse_name else None

                session.execute(
                    insert(NetkeibaRaceExtra).values(
                        race_id=race_id,
                        horse_id=horse_id,
                        remarks=remarks,
                    ).on_conflict_do_update(
                        constraint="uq_netkeiba_race_extras_race_horse",
                        set_={
                            "remarks": remarks,
                            "scraped_at": "now()",
                        },
                    )
                )

                if _is_disadvantage(remarks):
                    disadvantage_updates.append((race_id, horse_id))
                    logger.info(
                        "不利フラグ ON: race=%d horse=%d remarks=%r",
                        race_id, horse_id, remarks,
                    )

                stored_count += 1

            logger.info("race_id=%d (%s): %d 頭バッファ済み", race_id, jravan_race_id, len(pending))

        # ── 1日分をまとめて1回コミット（レース毎コミットを廃止） ──────────────
        for race_id, horse_id in disadvantage_updates:
            session.execute(
                update(CalculatedIndex)
                .where(CalculatedIndex.race_id == race_id)
                .where(CalculatedIndex.horse_id == horse_id)
                .values(disadvantage_flag=True)
            )
        session.commit()
        logger.info("date=%s: %d ペアを一括コミット完了", date, stored_count)

    except Exception:
        session.rollback()
        raise
    finally:
        if own_client:
            client.close()

    logger.info("date=%s 完了: %d ペア格納", date, stored_count)
    return stored_count


def import_race_remarks_for_month(session: Session, year_month: str) -> int:
    """指定月の全開催日を1セッションで一括スクレイピングする。

    import_race_remarks_direct を複数日にまたいで呼ぶ際に、
    毎日ログインが発生するのを防ぐためのラッパー。

    Args:
        session: SQLAlchemy セッション
        year_month: 対象年月 (YYYYMM)

    Returns:
        DB格納した (race_id, horse_id) ペア数（月累計）
    """
    import calendar
    from ..db.models import Race

    year = int(year_month[:4])
    month = int(year_month[4:6])
    start = f"{year_month}01"
    _, last_day = calendar.monthrange(year, month)
    end = f"{year_month}{last_day:02d}"

    dates: list[str] = session.execute(
        select(Race.date)
        .where(Race.date >= start)
        .where(Race.date <= end)
        .where(Race.jravan_race_id.is_not(None))
        .distinct()
        .order_by(Race.date)
    ).scalars().all()

    if not dates:
        logger.warning("year_month=%s に開催日が見つかりません", year_month)
        return 0

    user_id = os.environ.get("NETKEIBA_USER_ID", "")
    password = os.environ.get("NETKEIBA_PASSWORD", "")
    if not user_id or not password:
        raise RuntimeError("NETKEIBA_USER_ID / NETKEIBA_PASSWORD が未設定です")

    logger.info(
        "year_month=%s: %d 開催日をログイン1回で処理開始（日付間 %d 秒待機）",
        year_month, len(dates), _DATE_INTERVAL_SECONDS,
    )
    client = create_session(user_id, password)
    month_total = 0

    try:
        for i, date in enumerate(dates):
            count = import_race_remarks_direct(session, date, client=client)
            month_total += count

            # 最終日以外は待機してVPS PostgreSQLのI/O負荷を分散する
            if i < len(dates) - 1:
                logger.info(
                    "date=%s 完了。次の日付まで %d 秒待機...",
                    date, _DATE_INTERVAL_SECONDS,
                )
                time.sleep(_DATE_INTERVAL_SECONDS)
    finally:
        client.close()

    logger.info("year_month=%s 完了: %d ペア格納", year_month, month_total)
    return month_total
