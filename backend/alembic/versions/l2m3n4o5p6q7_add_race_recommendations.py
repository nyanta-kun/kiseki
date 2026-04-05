"""Add race_recommendations table

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-04-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "l2m3n4o5p6q7"
down_revision: str = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "race_recommendations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(8), nullable=False, comment="開催日 YYYYMMDD"),
        sa.Column("rank", sa.Integer(), nullable=False, comment="推奨順位 1〜5"),
        sa.Column("race_id", sa.Integer(), sa.ForeignKey("keiba.races.id"), nullable=False),
        sa.Column("bet_type", sa.String(20), nullable=False, comment="win/place/quinella"),
        sa.Column(
            "target_horses",
            postgresql.JSONB(),
            nullable=False,
            comment="推奨馬リスト [{horse_number, horse_name, ...}]",
        ),
        sa.Column(
            "snapshot_win_odds",
            postgresql.JSONB(),
            nullable=True,
            comment="スナップショット時点の単勝オッズ {horse_number: odds}",
        ),
        sa.Column(
            "snapshot_place_odds",
            postgresql.JSONB(),
            nullable=True,
            comment="スナップショット時点の複勝オッズ {horse_number: odds}",
        ),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True, comment="オッズスナップショット取得時刻"),
        sa.Column("reason", sa.Text(), nullable=False, comment="Claudeによる推奨理由（日本語）"),
        sa.Column("confidence", sa.Float(), nullable=False, comment="推奨信頼スコア 0〜1"),
        sa.Column("result_correct", sa.Boolean(), nullable=True, comment="推奨馬券が的中したか"),
        sa.Column("result_payout", sa.Integer(), nullable=True, comment="払戻金額（円/100円購入あたり）"),
        sa.Column("result_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="keiba",
    )
    op.create_index("ix_race_recommendations_date", "race_recommendations", ["date"], schema="keiba")
    op.create_index("ix_race_recommendations_race_id", "race_recommendations", ["race_id"], schema="keiba")


def downgrade() -> None:
    op.drop_index("ix_race_recommendations_race_id", table_name="race_recommendations", schema="keiba")
    op.drop_index("ix_race_recommendations_date", table_name="race_recommendations", schema="keiba")
    op.drop_table("race_recommendations", schema="keiba")
