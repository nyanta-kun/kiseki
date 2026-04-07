"""add_chihou_calculated_indices

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-04-07

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "p6q7r8s9t0u1"
down_revision: str = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """chihou.calculated_indices テーブルを追加する。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names(schema="chihou")

    if "calculated_indices" not in existing_tables:
        op.create_table(
            "calculated_indices",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "race_id",
                sa.Integer(),
                sa.ForeignKey("chihou.races.id", ondelete="CASCADE"),
                nullable=False,
                comment="chihou.races.id",
            ),
            sa.Column(
                "horse_id",
                sa.Integer(),
                sa.ForeignKey("chihou.horses.id", ondelete="CASCADE"),
                nullable=False,
                comment="chihou.horses.id",
            ),
            sa.Column("version", sa.Integer(), nullable=False, default=1, comment="計算ロジックバージョン"),
            sa.Column("speed_index", sa.Float(), nullable=True, comment="スピード指数（0-100, 平均50）"),
            sa.Column("last3f_index", sa.Float(), nullable=True, comment="後3ハロン指数（0-100, 平均50）"),
            sa.Column("jockey_index", sa.Float(), nullable=True, comment="騎手指数（0-100, 平均50）"),
            sa.Column("rotation_index", sa.Float(), nullable=True, comment="ローテーション指数（0-100）"),
            sa.Column("composite_index", sa.Float(), nullable=True, comment="総合指数（0-100）"),
            sa.Column("win_probability", sa.Float(), nullable=True, comment="推定単勝確率（0-1）"),
            sa.Column("place_probability", sa.Float(), nullable=True, comment="推定複勝確率（0-1）"),
            sa.Column(
                "calculated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
                comment="算出日時",
            ),
            sa.UniqueConstraint("race_id", "horse_id", "version", name="uq_chihou_calc_idx_race_horse_ver"),
            schema="chihou",
        )

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("calculated_indices", schema="chihou")} if "calculated_indices" in existing_tables else set()

    if "ix_chihou_calculated_indices_race_id" not in existing_indexes:
        op.create_index(
            "ix_chihou_calculated_indices_race_id",
            "calculated_indices",
            ["race_id"],
            schema="chihou",
        )
    if "ix_chihou_calculated_indices_horse_id" not in existing_indexes:
        op.create_index(
            "ix_chihou_calculated_indices_horse_id",
            "calculated_indices",
            ["horse_id"],
            schema="chihou",
        )


def downgrade() -> None:
    op.drop_index("ix_chihou_calculated_indices_horse_id", table_name="calculated_indices", schema="chihou")
    op.drop_index("ix_chihou_calculated_indices_race_id", table_name="calculated_indices", schema="chihou")
    op.drop_table("calculated_indices", schema="chihou")
