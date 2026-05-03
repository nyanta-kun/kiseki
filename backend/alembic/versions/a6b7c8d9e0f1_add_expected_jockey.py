"""Add expected_jockey_name column to special_registrations

Revision ID: a6b7c8d9e0f1
Revises: z5a6b7c8d9e0
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: str = "z5a6b7c8d9e0"


def upgrade() -> None:
    op.add_column(
        "special_registrations",
        sa.Column(
            "expected_jockey_name",
            sa.String(50),
            nullable=True,
            comment="想定騎手名（netkeiba shutuba.html スクレイピング由来。出馬表確定前の参考値）",
        ),
        schema="keiba",
    )
    op.add_column(
        "special_registrations",
        sa.Column(
            "expected_jockey_fetched_at",
            sa.TIMESTAMP(),
            nullable=True,
            comment="想定騎手取得タイムスタンプ",
        ),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("special_registrations", "expected_jockey_fetched_at", schema="keiba")
    op.drop_column("special_registrations", "expected_jockey_name", schema="keiba")
