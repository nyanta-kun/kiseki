"""Add jvan_time_dm and jvan_battle_dm to race_entries

Revision ID: w2x3y4z5a6b7
Revises: v1w2x3y4z5a6
Create Date: 2026-04-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "w2x3y4z5a6b7"
down_revision: str = "v1w2x3y4z5a6"


def upgrade() -> None:
    op.add_column(
        "race_entries",
        sa.Column("jvan_time_dm", sa.Numeric(5, 1), nullable=True,
                  comment="JRA-VAN NEXT タイム型DM指数（例: 43.1）"),
        schema="keiba",
    )
    op.add_column(
        "race_entries",
        sa.Column("jvan_battle_dm", sa.Integer(), nullable=True,
                  comment="JRA-VAN NEXT 対戦型DM指数（整数）"),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("race_entries", "jvan_battle_dm", schema="keiba")
    op.drop_column("race_entries", "jvan_time_dm", schema="keiba")
