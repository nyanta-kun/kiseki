"""add keirin model_evaluation table

Revision ID: e1f2g3h4i5j6
Revises: d2e3f4a5b6c7
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e1f2g3h4i5j6"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.create_table(
        "model_evaluation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("model_name", sa.String(50), nullable=False),
        sa.Column("period_from", sa.String(10), nullable=False),
        sa.Column("period_to", sa.String(10), nullable=False),
        sa.Column("period_type", sa.String(10), nullable=False),
        sa.Column("n_picks", sa.Integer(), nullable=False),
        sa.Column("n_hits", sa.Integer(), nullable=False),
        sa.Column("total_bet", sa.Integer(), nullable=False),
        sa.Column("total_payout", sa.Integer(), nullable=False),
        sa.Column("roi", sa.Numeric(6, 3)),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("model_name", "period_type", name="uq_model_eval_model_period"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("model_evaluation", schema=SCHEMA)
