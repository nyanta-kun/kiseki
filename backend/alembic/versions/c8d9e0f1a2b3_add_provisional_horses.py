"""Add provisional_horses table for pre-JVLink 2-year-old horses from netkeiba

Revision ID: c8d9e0f1a2b3
Revises: a6b7c8d9e0f1
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str = "a6b7c8d9e0f1"


def upgrade() -> None:
    op.create_table(
        "provisional_horses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("netkeiba_horse_id", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("birth_year", sa.Integer(), nullable=True),
        sa.Column("birth_date", sa.String(8), nullable=True),
        sa.Column("sex", sa.String(10), nullable=True),
        sa.Column("coat_color", sa.String(20), nullable=True),
        sa.Column("sire_name", sa.String(100), nullable=True),
        sa.Column("dam_name", sa.String(100), nullable=True),
        sa.Column("broodmare_sire_name", sa.String(100), nullable=True),
        sa.Column("trainer_name", sa.String(100), nullable=True),
        sa.Column("owner_name", sa.String(100), nullable=True),
        sa.Column("farm_name", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("merged_horse_id", sa.Integer(), nullable=True),
        sa.Column("merged_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["merged_horse_id"], ["keiba.horses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("netkeiba_horse_id", name="uq_provisional_horse_netkeiba_id"),
        schema="keiba",
    )
    op.create_index(
        "ix_provisional_horses_netkeiba_horse_id",
        "provisional_horses", ["netkeiba_horse_id"], schema="keiba",
    )
    op.create_index(
        "ix_provisional_horses_name",
        "provisional_horses", ["name"], schema="keiba",
    )


def downgrade() -> None:
    op.drop_index("ix_provisional_horses_name", table_name="provisional_horses", schema="keiba")
    op.drop_index("ix_provisional_horses_netkeiba_horse_id", table_name="provisional_horses", schema="keiba")
    op.drop_table("provisional_horses", schema="keiba")
