"""Alter jvan_battle_dm from integer to numeric(5,1) for score storage

Revision ID: x3y4z5a6b7c8
Revises: w2x3y4z5a6b7
Create Date: 2026-04-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "x3y4z5a6b7c8"
down_revision: str = "w2x3y4z5a6b7"


def upgrade() -> None:
    op.alter_column(
        "race_entries",
        "jvan_battle_dm",
        type_=sa.Numeric(5, 1),
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment="JRA-VAN NEXT 対戦型DM指数スコア（例: 80.7）",
        schema="keiba",
        postgresql_using="jvan_battle_dm::numeric(5,1)",
    )


def downgrade() -> None:
    op.alter_column(
        "race_entries",
        "jvan_battle_dm",
        type_=sa.Integer(),
        existing_type=sa.Numeric(5, 1),
        existing_nullable=True,
        comment="JRA-VAN NEXT 対戦型DM指数（整数）",
        schema="keiba",
        postgresql_using="jvan_battle_dm::integer",
    )
