"""Add v13 indices: career_phase, distance_change, jockey_trainer_combo, going_pedigree

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-04-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "s9t0u1v2w3x4"
down_revision: str = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calculated_indices",
        sa.Column(
            "career_phase_index",
            sa.Numeric(5, 1),
            nullable=True,
            comment="成長曲線指数（直近N走のトレンドと馬齢フェーズ, 中立=50）",
        ),
        schema="keiba",
    )
    op.add_column(
        "calculated_indices",
        sa.Column(
            "distance_change_index",
            sa.Numeric(5, 1),
            nullable=True,
            comment="距離変更適性指数（延長/短縮パターン別成績, 中立=50）",
        ),
        schema="keiba",
    )
    op.add_column(
        "calculated_indices",
        sa.Column(
            "jockey_trainer_combo_index",
            sa.Numeric(5, 1),
            nullable=True,
            comment="騎手×厩舎コンビ指数（コンビ勝率 vs 単独騎手勝率, 中立=50）",
        ),
        schema="keiba",
    )
    op.add_column(
        "calculated_indices",
        sa.Column(
            "going_pedigree_index",
            sa.Numeric(5, 1),
            nullable=True,
            comment="重馬場×血統指数（重/不良馬場での父系統適性, 中立=50）",
        ),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("calculated_indices", "going_pedigree_index", schema="keiba")
    op.drop_column("calculated_indices", "jockey_trainer_combo_index", schema="keiba")
    op.drop_column("calculated_indices", "distance_change_index", schema="keiba")
    op.drop_column("calculated_indices", "career_phase_index", schema="keiba")
