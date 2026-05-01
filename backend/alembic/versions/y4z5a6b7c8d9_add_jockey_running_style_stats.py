"""Add jockey_running_style_stats table for v25 騎手戦法統合

Revision ID: y4z5a6b7c8d9
Revises: x3y4z5a6b7c8
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "y4z5a6b7c8d9"
down_revision: str = "x3y4z5a6b7c8"


def upgrade() -> None:
    op.create_table(
        "jockey_running_style_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jockey_id", sa.Integer(), sa.ForeignKey("keiba.jockeys.id"), nullable=False, index=True),
        sa.Column(
            "window_months",
            sa.Integer(),
            nullable=False,
            server_default="24",
            comment="集計対象の月数（直近24ヶ月）",
        ),
        sa.Column("total_rides", sa.Integer(), nullable=False, comment="集計対象期間の騎乗数"),
        sa.Column("escape_rate", sa.Numeric(4, 3), comment="逃げ率（passing_4/head_count < 0.10）"),
        sa.Column("leader_rate", sa.Numeric(4, 3), comment="先行率（< 0.30）"),
        sa.Column("mid_rate", sa.Numeric(4, 3), comment="中団率（< 0.65）"),
        sa.Column("closer_rate", sa.Numeric(4, 3), comment="後方率（>= 0.65）"),
        sa.Column("makuri_rate", sa.Numeric(4, 3), comment="マクリ率（4C順位 vs 1C順位で5人以上順位上昇）"),
        sa.Column(
            "diversity",
            sa.Numeric(4, 3),
            comment="戦法多様性 1 - Σpᵢ²（低=特化型, 高=柔軟型）",
        ),
        sa.Column(
            "calculated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            comment="集計日時",
        ),
        sa.UniqueConstraint("jockey_id", "window_months", name="uq_jockey_style_window"),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_table("jockey_running_style_stats", schema="keiba")
