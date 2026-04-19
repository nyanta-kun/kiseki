"""Add keiba.breeding_horses for persistent HN (pedigree) cache

Revision ID: v1w2x3y4z5a6
Revises: u1v2w3x4y5z6
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op

revision: str = "v1w2x3y4z5a6"
down_revision: str = "u1v2w3x4y5z6"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS keiba.breeding_horses (
            breeding_code TEXT NOT NULL,
            name TEXT,
            name_en TEXT,
            CONSTRAINT pk_breeding_horses PRIMARY KEY (breeding_code)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS keiba.breeding_horses")
