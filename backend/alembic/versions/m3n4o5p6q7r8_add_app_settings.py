"""add_app_settings

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-04-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m3n4o5p6q7r8"
down_revision: Union[str, None] = "l2m3n4o5p6q7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """keiba.app_settings テーブルを作成し PAID_MODE 初期値を挿入する。"""
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(100), primary_key=True, comment="設定キー"),
        sa.Column("value", sa.String(500), nullable=False, comment="設定値"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(100), nullable=True),
        schema="keiba",
    )
    op.execute("INSERT INTO keiba.app_settings (key, value) VALUES ('PAID_MODE', 'false')")


def downgrade() -> None:
    """keiba.app_settings テーブルを削除する。"""
    op.drop_table("app_settings", schema="keiba")
