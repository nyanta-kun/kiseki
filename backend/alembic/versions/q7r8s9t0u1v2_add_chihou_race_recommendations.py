"""add chihou race_recommendations

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-04-08

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "q7r8s9t0u1v2"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "race_recommendations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(8), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("bet_type", sa.String(20), nullable=False),
        sa.Column("target_horses", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot_win_odds", postgresql.JSONB(), nullable=True),
        sa.Column("snapshot_place_odds", postgresql.JSONB(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("odds_decision", sa.String(10), nullable=True),
        sa.Column("odds_decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("odds_decision_reason", sa.Text(), nullable=True),
        sa.Column("result_correct", sa.Boolean(), nullable=True),
        sa.Column("result_payout", sa.Integer(), nullable=True),
        sa.Column("result_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["race_id"], ["chihou.races.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="chihou",
    )
    op.create_index("ix_chihou_race_recommendations_date", "race_recommendations", ["date"], schema="chihou")
    op.create_index("ix_chihou_race_recommendations_race_id", "race_recommendations", ["race_id"], schema="chihou")


def downgrade() -> None:
    op.drop_index("ix_chihou_race_recommendations_race_id", table_name="race_recommendations", schema="chihou")
    op.drop_index("ix_chihou_race_recommendations_date", table_name="race_recommendations", schema="chihou")
    op.drop_table("race_recommendations", schema="chihou")
