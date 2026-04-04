"""Add yoso public settings: is_yoso_public, yoso_name to users

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-04-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "k1l2m3n4o5p6"
down_revision: str = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_yoso_public",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="予想を他ユーザーに公開するか",
        ),
        schema="keiba",
    )
    op.add_column(
        "users",
        sa.Column(
            "yoso_name",
            sa.String(50),
            nullable=True,
            comment="予想公開時の表示名（予想名）",
        ),
        schema="keiba",
    )
    op.create_unique_constraint(
        "uq_users_yoso_name",
        "users",
        ["yoso_name"],
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_yoso_name", "users", schema="keiba")
    op.drop_column("users", "yoso_name", schema="keiba")
    op.drop_column("users", "is_yoso_public", schema="keiba")
