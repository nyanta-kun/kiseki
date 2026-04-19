"""Add keiba.breeding_horses for persistent HN (pedigree) cache

Revision ID: v1w2x3y4z5a6
Revises: u1v2w3x4y5z6
Create Date: 2026-04-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "v1w2x3y4z5a6"
down_revision: str = "u1v2w3x4y5z6"


def upgrade() -> None:
    op.execute("SET search_path TO keiba")
    op.create_table(
        "breeding_horses",
        sa.Column("breeding_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("name_en", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("breeding_code"),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_table("breeding_horses", schema="keiba")
