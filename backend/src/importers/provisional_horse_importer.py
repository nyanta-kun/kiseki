"""暫定馬マスタ（JV-Link未登録2歳馬）のインポートとマージ処理。

netkeiba からスクレイプした2歳馬データを provisional_horses に UPSERT し、
JV-Link SE レコードで初出走が確認された時点で keiba.horses へ自動マージする。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Horse, Pedigree, ProvisionalHorse

logger = logging.getLogger(__name__)


async def upsert_provisional_horses(
    db: AsyncSession, horses: list[dict[str, Any]]
) -> dict[str, int]:
    """netkeiba スクレイプ馬データを provisional_horses へ UPSERT する。

    Args:
        horses: [
            {
                "netkeiba_horse_id": str,   # 必須
                "name": str,                # 必須（カタカナ）
                "birth_year": int | None,
                "birth_date": str | None,   # YYYYMMDD
                "sex": str | None,
                "coat_color": str | None,
                "sire_name": str | None,
                "dam_name": str | None,
                "broodmare_sire_name": str | None,
                "trainer_name": str | None,
                "owner_name": str | None,
                "farm_name": str | None,
            }, ...
        ]

    Returns:
        {"inserted": int, "updated": int, "skipped": int}
    """
    if not horses:
        return {"inserted": 0, "updated": 0, "skipped": 0}

    # すでにマージ済みのレコードは更新しない
    existing_ids_result = await db.execute(
        select(ProvisionalHorse.netkeiba_horse_id, ProvisionalHorse.merged_horse_id).where(
            ProvisionalHorse.netkeiba_horse_id.in_([h["netkeiba_horse_id"] for h in horses])
        )
    )
    existing = {row.netkeiba_horse_id: row.merged_horse_id for row in existing_ids_result}

    to_upsert = []
    skipped = 0
    for h in horses:
        nk_id = h.get("netkeiba_horse_id", "")
        if not nk_id or not h.get("name"):
            skipped += 1
            continue
        # マージ済みはスキップ
        if existing.get(nk_id) is not None:
            skipped += 1
            continue
        to_upsert.append(
            {
                "netkeiba_horse_id": nk_id,
                "name": h["name"],
                "birth_year": h.get("birth_year"),
                "birth_date": h.get("birth_date"),
                "sex": h.get("sex"),
                "coat_color": h.get("coat_color"),
                "sire_name": h.get("sire_name"),
                "dam_name": h.get("dam_name"),
                "broodmare_sire_name": h.get("broodmare_sire_name"),
                "trainer_name": h.get("trainer_name"),
                "owner_name": h.get("owner_name"),
                "farm_name": h.get("farm_name"),
            }
        )

    if not to_upsert:
        return {"inserted": 0, "updated": 0, "skipped": skipped}

    stmt = insert(ProvisionalHorse).values(to_upsert)
    update_cols = [
        "name", "birth_year", "birth_date", "sex", "coat_color",
        "sire_name", "dam_name", "broodmare_sire_name",
        "trainer_name", "owner_name", "farm_name", "updated_at",
    ]
    stmt = stmt.on_conflict_do_update(
        index_elements=["netkeiba_horse_id"],
        set_={col: stmt.excluded[col] for col in update_cols if col != "updated_at"},
    )
    await db.execute(stmt)
    await db.flush()

    new_ids = {h["netkeiba_horse_id"] for h in to_upsert}
    inserted = sum(1 for nk_id in new_ids if nk_id not in existing)
    updated = len(new_ids) - inserted

    logger.info("provisional_horses upsert: inserted=%d, updated=%d, skipped=%d", inserted, updated, skipped)
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


async def try_merge_provisional(
    db: AsyncSession, jravan_code: str, horse_name: str, birth_year: int | None
) -> int | None:
    """新規 Horse（初出走）と provisional_horses の照合・マージを試みる。

    JV-Link SE レコードで新しい馬が登録された直後に呼ぶ。
    照合キー: 馬名（完全一致） + 生産年（一致 or provisional が NULL）
    マージ時: provisional.merged_horse_id と pedigrees に sire/dam をセット。

    Args:
        jravan_code: keiba.horses.jravan_code（新規登録された馬）
        horse_name: 馬名カタカナ
        birth_year: 生産年（SE レコードの race_date[:4] から推定）

    Returns:
        マージした provisional_horses.id（見つからない場合 None）
    """
    if not horse_name:
        return None

    # netkeiba_horse_id が jravan_code と一致するケースを優先チェック
    # （同一の血統登録番号を使っている場合）
    stmt = select(ProvisionalHorse).where(
        ProvisionalHorse.merged_horse_id.is_(None),
        ProvisionalHorse.netkeiba_horse_id == jravan_code,
    )
    result = await db.execute(stmt)
    prov = result.scalar_one_or_none()

    if prov is None:
        # 馬名 + 生産年で照合
        name_stmt = select(ProvisionalHorse).where(
            ProvisionalHorse.merged_horse_id.is_(None),
            ProvisionalHorse.name == horse_name,
        )
        if birth_year:
            name_stmt = name_stmt.where(
                (ProvisionalHorse.birth_year == birth_year)
                | ProvisionalHorse.birth_year.is_(None)
            )
        name_stmt = name_stmt.order_by(
            # birth_year が一致するものを優先
            ProvisionalHorse.birth_year.is_(None),
        ).limit(1)
        result = await db.execute(name_stmt)
        prov = result.scalar_one_or_none()

    if prov is None:
        return None

    # Horse.id を取得
    horse_row = await db.execute(
        select(Horse.id).where(Horse.jravan_code == jravan_code)
    )
    horse_id = horse_row.scalar_one_or_none()
    if horse_id is None:
        return None

    # provisional → Horse へ情報補完
    await db.execute(
        update(Horse)
        .where(Horse.id == horse_id)
        .values(
            birthday=prov.birth_date or "",
            coat_color=prov.coat_color,
            owner=prov.owner_name,
            breeder=prov.farm_name,
        )
    )

    # Pedigree へ sire/dam を補完（既存レコードの NULL のみ更新）
    if prov.sire_name or prov.dam_name or prov.broodmare_sire_name:
        existing_ped = await db.execute(
            select(Pedigree).where(Pedigree.horse_id == horse_id)
        )
        ped = existing_ped.scalar_one_or_none()
        if ped is None:
            db.add(
                Pedigree(
                    horse_id=horse_id,
                    sire=prov.sire_name,
                    dam=prov.dam_name,
                    sire_of_dam=prov.broodmare_sire_name,
                )
            )
        else:
            if ped.sire is None and prov.sire_name:
                ped.sire = prov.sire_name
            if ped.dam is None and prov.dam_name:
                ped.dam = prov.dam_name
            if ped.sire_of_dam is None and prov.broodmare_sire_name:
                ped.sire_of_dam = prov.broodmare_sire_name

    # provisional にマージ済みをマーク
    prov.merged_horse_id = horse_id
    prov.merged_at = datetime.now()

    await db.flush()
    logger.info(
        "provisional_horses マージ完了: id=%d name=%s → horse_id=%d",
        prov.id, prov.name, horse_id,
    )
    return prov.id
