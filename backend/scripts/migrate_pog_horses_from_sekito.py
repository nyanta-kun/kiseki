#!/usr/bin/env python3
"""`sekito.horse` を `keiba.pog_horses` へ一度だけ引き継ぐ（統合 Phase 5 の 5a-3）。

## なぜスクレイパだけでは足りないか

`scrape_pog_horses.py` は netkeiba の馬齢検索なので**その時点の1世代しか取れない**。
POG は 2005 年から続いていて過去の指名馬（22年分・1,388頭）の情報も画面が使う。
古い世代を年齢指定で取り直すと、70頭のために 7,000頭を舐めることになる。
既に `sekito.horse`(16,926行) に揃っているので、そこから写す。

    2026-09-08 実測: pog_user の指名馬 1,388頭は **全頭 sekito.horse に居る**

## 🔴 捏造された生年月日を持ち込まない

`sekito.horse.birthday` の月日別分布:

    01-01   7,859 件   ← 2024年産 7,855 がまるごとここ
    04-07     114 件
    04-09     113 件   ← 本物はサラブレッドらしく3〜4月に集まる

2024年産が全件 01-01 なのは、bulk スクレイパが一覧に無い生年月日を
`f'{birth_year}-01-01'` と**埋めていた**から。**1月1日生まれのサラブレッドは
実質存在しない**ので、01-01 は捏造とみなして NULL で入れる。
生産年は `birth_year` 列に入るので情報は落ちない。

## 既にある行は上書きしない

`keiba.pog_horses` に既にある行はスクレイパが入れた新しいデータなので、
**NULL の列だけ埋める**（COALESCE の向きが `scrape_pog_horses` と逆）。

## 使い方

    # 件数だけ見る（既定）
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/migrate_pog_horses_from_sekito.py

    # 実際に書く
    ... scripts/migrate_pog_horses_from_sekito.py --apply

一度きりの引っ越し用。sekito スキーマを落とすときに一緒に消してよい。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import text

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402

# 生年月日は 01-01 を捨てる。生産年は birthday → netkeiba_horse_id の順で決める。
SELECT_SQL = text(
    """
    SELECT
        h.netkeiba_horse_id,
        NULLIF(h.name, '')                                    AS name,
        h.sex,
        COALESCE(
            EXTRACT(YEAR FROM h.birthday)::int,
            CASE WHEN h.netkeiba_horse_id ~ '^[0-9]{4}'
                 THEN left(h.netkeiba_horse_id, 4)::int END
        )                                                     AS birth_year,
        CASE WHEN to_char(h.birthday, 'MM-DD') = '01-01' THEN NULL
             ELSE h.birthday END                              AS birthday,
        h.sire, h.broodmare, h.broodmare_sire, h.stable, h.owner
    FROM sekito.horse h
    -- netkeiba の馬ID は数字10桁とは限らない（外国産馬は `000a02d612`）。
    -- ただし `top.` のような明らかなゴミ行が 1 件混ざっているので英数字だけに絞る。
    WHERE h.netkeiba_horse_id ~ '^[0-9a-zA-Z]+$'
    ORDER BY h.netkeiba_horse_id
    """
)

# 🔴 COALESCE の向きが scrape_pog_horses と逆。既存（スクレイパ由来）を勝たせる。
UPSERT_SQL = text(
    """
    INSERT INTO keiba.pog_horses
        (netkeiba_horse_id, name, sex, birth_year, birthday,
         sire, broodmare, broodmare_sire, stable, owner)
    VALUES
        (:netkeiba_horse_id, :name, :sex, :birth_year, :birthday,
         :sire, :broodmare, :broodmare_sire, :stable, :owner)
    ON CONFLICT (netkeiba_horse_id) DO UPDATE SET
        name           = COALESCE(keiba.pog_horses.name,           EXCLUDED.name),
        sex            = COALESCE(keiba.pog_horses.sex,            EXCLUDED.sex),
        birth_year     = COALESCE(keiba.pog_horses.birth_year,     EXCLUDED.birth_year),
        birthday       = COALESCE(keiba.pog_horses.birthday,       EXCLUDED.birthday),
        sire           = COALESCE(keiba.pog_horses.sire,           EXCLUDED.sire),
        broodmare      = COALESCE(keiba.pog_horses.broodmare,      EXCLUDED.broodmare),
        broodmare_sire = COALESCE(keiba.pog_horses.broodmare_sire, EXCLUDED.broodmare_sire),
        stable         = COALESCE(keiba.pog_horses.stable,         EXCLUDED.stable),
        owner          = COALESCE(keiba.pog_horses.owner,          EXCLUDED.owner)
    """
)

BATCH = 1000


def main() -> int:
    parser = argparse.ArgumentParser(
        description="sekito.horse を keiba.pog_horses へ引き継ぐ（一度きり）"
    )
    parser.add_argument("--apply", action="store_true",
                        help="実際に書き込む（既定は件数の確認のみ）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    with SyncSessionLocal() as session:
        rows = [dict(r) for r in session.execute(SELECT_SQL).mappings()]
        before = session.execute(
            text("SELECT count(*) FROM keiba.pog_horses")
        ).scalar_one()

        fabricated = sum(1 for r in rows if r["birthday"] is None and r["birth_year"])
        logging.info(
            "sekito.horse: %d 行 / うち生年月日を落とす（01-01）行を含む %d 行",
            len(rows), fabricated,
        )
        logging.info("keiba.pog_horses の現在: %d 行", before)

        if not args.apply:
            logging.info("--apply が無いので書き込みません")
            return 0

        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            session.execute(UPSERT_SQL, chunk)
            session.commit()
            logging.info("  %d/%d 件", min(i + BATCH, len(rows)), len(rows))

        after = session.execute(
            text("SELECT count(*) FROM keiba.pog_horses")
        ).scalar_one()
        missing = session.execute(
            text(
                """
                SELECT count(DISTINCT pu.netkeiba_horse_id)
                FROM sekito.pog_user pu
                LEFT JOIN keiba.pog_horses p
                       ON p.netkeiba_horse_id = pu.netkeiba_horse_id
                WHERE pu."order" != 0 AND p.id IS NULL
                """
            )
        ).scalar_one()
        logging.info("keiba.pog_horses: %d → %d 行", before, after)
        logging.info("指名馬でマスタに無いもの: %d 頭", missing)
        if missing:
            logging.warning("指名馬が %d 頭欠けています", missing)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
