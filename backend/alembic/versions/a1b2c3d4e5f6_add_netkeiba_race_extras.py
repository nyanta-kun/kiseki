"""add netkeiba_race_extras table

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-03-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "netkeiba_race_extras",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("horse_id", sa.Integer(), nullable=False),
        sa.Column("remarks", sa.String(length=200), nullable=True,
                  comment="備考（出遅れ・不利・後方一気等の短評テキスト）"),
        sa.Column("notable_comment", sa.Text(), nullable=True,
                  comment="注目馬レース後の短評（プレミアム）"),
        sa.Column("race_analysis", sa.Text(), nullable=True,
                  comment="分析コメント（レース全体の流れ、全馬共通）"),
        sa.Column("scraped_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["race_id"], ["keiba.races.id"]),
        sa.ForeignKeyConstraint(["horse_id"], ["keiba.horses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("race_id", "horse_id", name="uq_netkeiba_race_extras_race_horse"),
        schema="keiba",
    )
    op.create_index(
        "ix_netkeiba_race_extras_race_id",
        "netkeiba_race_extras",
        ["race_id"],
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_index("ix_netkeiba_race_extras_race_id", table_name="netkeiba_race_extras", schema="keiba")
    op.drop_table("netkeiba_race_extras", schema="keiba")
