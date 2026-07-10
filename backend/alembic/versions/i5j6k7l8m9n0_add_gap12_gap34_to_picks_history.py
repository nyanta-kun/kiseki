"""add gap12 / gap34 columns to keirin picks_history

gap12: 指数1位と2位の予測確率差（0-1スケール）
gap34: 指数3位と4位の予測確率差（0-1スケール）
ランク別の「指数条件のみの候補数」算出に使用する
（SS: gap12>=0.10 ∧ gap23>=1pt / S: gap12>=0.15 / S+: gap12>=0.25 ∧ gap34>=0.04）。

Revision ID: i5j6k7l8m9n0
Revises: h4i5j6k7l8m9
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "i5j6k7l8m9n0"
down_revision = "h4i5j6k7l8m9"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column("picks_history", sa.Column("gap12", sa.Numeric(6, 4), nullable=True), schema=SCHEMA)
    op.add_column("picks_history", sa.Column("gap34", sa.Numeric(6, 4), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("picks_history", "gap34", schema=SCHEMA)
    op.drop_column("picks_history", "gap12", schema=SCHEMA)
