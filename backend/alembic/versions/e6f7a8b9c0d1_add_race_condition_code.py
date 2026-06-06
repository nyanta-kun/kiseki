"""Add races.race_condition_code (コード表2007 競走条件コード)

新馬(701)/未勝利(703)/各勝クラス(005/010/016) を正確に区別するため、
RA レコードの競走条件コードを保持するカラムを追加する。

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str = "d5e6f7a8b9c0"


def upgrade() -> None:
    op.add_column(
        "races",
        sa.Column(
            "race_condition_code",
            sa.String(3),
            nullable=True,
            comment="競走条件コード（コード表2007: 701=新馬,702=未出走,703=未勝利,005=1勝,010=2勝,016=3勝,999=OP）",
        ),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("races", "race_condition_code", schema="keiba")
