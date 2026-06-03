"""Add projected_entries table for netkeiba 出走想定 (全レース)

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str = "b3c4d5e6f7a8"


def upgrade() -> None:
    op.create_table(
        "projected_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("netkeiba_race_id", sa.String(12), nullable=False, comment="netkeibaレースID"),
        sa.Column("race_date", sa.String(8), nullable=False, comment="開催日 YYYYMMDD"),
        sa.Column("course_code", sa.String(2), nullable=False, comment="JRA場コード"),
        sa.Column("race_number", sa.Integer(), nullable=False, comment="レース番号"),
        sa.Column("race_name", sa.String(200), nullable=True, comment="競走名"),
        sa.Column("netkeiba_horse_id", sa.String(12), nullable=True, comment="netkeiba馬ID"),
        sa.Column("horse_name", sa.String(100), nullable=False, comment="馬名"),
        sa.Column("sex_age", sa.String(8), nullable=True, comment="性齢"),
        sa.Column("expected_jockey_name", sa.String(50), nullable=True, comment="想定騎手名"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("netkeiba_race_id", "horse_name", name="uq_projected_race_horse"),
        schema="keiba",
    )
    op.create_index(
        "ix_projected_race_id", "projected_entries", ["netkeiba_race_id"], schema="keiba"
    )
    op.create_index("ix_projected_date", "projected_entries", ["race_date"], schema="keiba")


def downgrade() -> None:
    op.drop_index("ix_projected_date", table_name="projected_entries", schema="keiba")
    op.drop_index("ix_projected_race_id", table_name="projected_entries", schema="keiba")
    op.drop_table("projected_entries", schema="keiba")
