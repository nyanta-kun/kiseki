"""drop keiba.provisional_horses (廃案・netkeiba 直接スクレイプへ置き換え済み)

2026-05-04 に作った JV-Link 未登録2歳馬の暫定マスタ。増分走査の設計上 93頭しか
拾えず、sekito 側の netkeiba 馬検索全件ページング（sekito.horse・2024年産 7,855頭）へ
明示的に置き換えられた。93行は1件もマージされないまま 2026-05-31 に
provisional_horses_deleted_20260531 へ退避され、本体は0行のまま放置されていた。

POG 移植（Phase 5）は本テーブルの復活ではなく、bulk スクレイパと馬マスタを
kiseki へ持つ形で行う。指名の結合キーは netkeiba_horse_id。

Revision ID: 202609081552_jra
Revises: 202609080008_jra
Create Date: 2026-09-08
"""

from alembic import op

revision = "202609081552_jra"
down_revision = "202609080008_jra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS keiba.provisional_horses_deleted_20260531")
    op.execute("DROP TABLE IF EXISTS keiba.provisional_horses")


def downgrade() -> None:
    """テーブル定義のみ復元する（データは戻らない）。"""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS keiba.provisional_horses (
            id SERIAL PRIMARY KEY,
            netkeiba_horse_id VARCHAR(20) NOT NULL,
            name VARCHAR(100) NOT NULL,
            birth_year INTEGER,
            birth_date VARCHAR(8),
            sex VARCHAR(10),
            coat_color VARCHAR(20),
            sire_name VARCHAR(100),
            dam_name VARCHAR(100),
            broodmare_sire_name VARCHAR(100),
            trainer_name VARCHAR(100),
            owner_name VARCHAR(100),
            farm_name VARCHAR(100),
            created_at TIMESTAMP DEFAULT now(),
            updated_at TIMESTAMP DEFAULT now(),
            merged_horse_id INTEGER REFERENCES keiba.horses(id),
            merged_at TIMESTAMP,
            CONSTRAINT uq_provisional_horse_netkeiba_id UNIQUE (netkeiba_horse_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_provisional_horses_netkeiba_horse_id "
        "ON keiba.provisional_horses (netkeiba_horse_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_provisional_horses_name "
        "ON keiba.provisional_horses (name)"
    )
