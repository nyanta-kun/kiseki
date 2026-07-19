"""add gate_label / win_rank / ratio columns to keirin picks_history

S3(M)のOR3ゲート（gap12 / win_rank / ratio）のうちどれで成立したかを記録する。
既存のgap12列はS3行では常にNULLで、事後にどのゲート由来の的中率が弱いか
分析できなかったための追加（2026-07-19）。

gate_label: 'gap12' / 'win_rank' / 'ratio'（m_axis_gateの判定ラベル）。S3以外はNULL
win_rank:   システム◎の1着モデル内レース順位（1-indexed）。S3以外はNULL
ratio:      システム◎のp_win/p_top3比。S3以外はNULL

Revision ID: l8m9n0p1q2r3
Revises: k7l8m9n0p1q2
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "l8m9n0p1q2r3"
down_revision = "k7l8m9n0p1q2"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column("picks_history", sa.Column("gate_label", sa.String(10), nullable=True), schema=SCHEMA)
    op.add_column("picks_history", sa.Column("win_rank", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("picks_history", sa.Column("ratio", sa.Numeric(6, 4), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("picks_history", "ratio", schema=SCHEMA)
    op.drop_column("picks_history", "win_rank", schema=SCHEMA)
    op.drop_column("picks_history", "gate_label", schema=SCHEMA)
