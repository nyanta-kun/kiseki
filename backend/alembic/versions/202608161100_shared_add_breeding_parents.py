"""add parent codes to keiba.breeding_horses（系図の再帰に必要）

`parse_hn` は HN（繁殖馬マスタ）から **父馬繁殖登録番号(pos230) / 母馬繁殖登録番号(pos240)**
を既に抽出しているのに、`breeding_horses` が `breeding_code / name / name_en` の
3列しか持たず**捨てていた**（`pedigrees` が3代14頭のうち3頭しか持たないのと同じ型）。

この2列を持てば **繁殖登録番号で何代でも再帰**できる:

    競走馬 → SK/UM の3代14頭 → 各祖先を HN で引く → その親 → …

インブリード判定に要る5代（62頭）に netkeiba のスクレイピングは不要になる。
繁殖馬マスタは種牡馬・繁殖牝馬とも 99% カバー済み
（実測 2026-08-16: 父 2,853/2,871・母 39,881/39,925）。

⚠️ **この migration だけでは値は入らない。** 既存 314,247 行は NULL のまま。
`jvlink_agent.py --mode bldn-full` を **realtime が止まる 22:30 JST 以降**に流すこと
（完了ファイル `data/completed/BLDN_FULL.txt` を消してから）。
台帳 `docs/chihou_rebuild_2026_08.md` 17.11。

ついでに `blood_code`（競走馬としての血統登録番号）/ `birth_year` / `sex_code` も持つ。
いずれも `parse_hn` が既に返しており、世代計算と突合の検証に要るため。

Revision ID: 202608161100_shared
Revises: 202608160830_keirin
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608161100_shared"
down_revision = "202608160830_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keiba"
TABLE = "breeding_horses"

COLUMNS = [
    ("sire_breeding_code", sa.Text(), "父馬繁殖登録番号（HN pos230）"),
    ("dam_breeding_code", sa.Text(), "母馬繁殖登録番号（HN pos240）"),
    ("blood_code", sa.Text(), "血統登録番号（競走馬として登録がある場合・HN pos30）"),
    ("birth_year", sa.Text(), "生年（HN pos197）"),
    ("sex_code", sa.Text(), "性別コード（HN pos201）"),
]


def upgrade() -> None:
    for name, type_, comment in COLUMNS:
        op.add_column(
            TABLE, sa.Column(name, type_, nullable=True, comment=comment), schema=SCHEMA
        )
    # 系図を辿るときは親コードから引くので索引を張る
    op.create_index(
        "ix_keiba_breeding_horses_sire", TABLE, ["sire_breeding_code"], schema=SCHEMA
    )
    op.create_index(
        "ix_keiba_breeding_horses_dam", TABLE, ["dam_breeding_code"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_keiba_breeding_horses_dam", table_name=TABLE, schema=SCHEMA)
    op.drop_index("ix_keiba_breeding_horses_sire", table_name=TABLE, schema=SCHEMA)
    for name, _type, _comment in COLUMNS:
        op.drop_column(TABLE, name, schema=SCHEMA)
