"""Add source column to special_registrations (toku / netkeiba)

netkeiba 由来の出走想定馬を special_registrations にも格納して sekito(POG出走予定)で
表示できるようにする。TOKU(特別登録)と区別するため source 列を追加。
kiseki は source='toku' のみ「特別登録」扱いとし、'netkeiba' は「出走想定」扱いにする。

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str = "c4d5e6f7a8b9"


def upgrade() -> None:
    op.add_column(
        "special_registrations",
        sa.Column(
            "source", sa.String(10), nullable=False, server_default="toku",
            comment="由来: toku(JV-Link特別登録) / netkeiba(出走想定スクレイプ)",
        ),
        schema="keiba",
    )
    # 既存行は TOKU 由来
    op.execute("UPDATE keiba.special_registrations SET source = 'toku' WHERE source IS NULL")


def downgrade() -> None:
    op.drop_column("special_registrations", "source", schema="keiba")
