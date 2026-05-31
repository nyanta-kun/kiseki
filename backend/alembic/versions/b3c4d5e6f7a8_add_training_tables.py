"""Add slope_training (HC/SLOP) and wood_training (WC/WOOD) tables

調教データ（坂路=2003年以降両トレセン / ウッド=2021-07-27以降美浦）を格納する。
血統登録番号（horses.jravan_code と一致）で馬に紐付く。

Revision ID: b3c4d5e6f7a8
Revises: f5a6b7c8d9e0
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str = "f5a6b7c8d9e0"


def upgrade() -> None:
    op.create_table(
        "slope_training",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("blood_reg_no", sa.String(10), nullable=False),
        sa.Column("training_date", sa.String(8), nullable=False),
        sa.Column("training_time", sa.String(4), nullable=True),
        sa.Column("center", sa.String(1), nullable=True),
        sa.Column("time_4f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_800_600", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_3f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_600_400", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_2f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_400_200", sa.Numeric(3, 1), nullable=True),
        sa.Column("lap_200_0", sa.Numeric(3, 1), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "blood_reg_no", "training_date", "training_time", "center",
            name="uq_slope_training_key",
        ),
        schema="keiba",
    )
    op.create_index(
        "ix_slope_training_blood_reg_no",
        "slope_training", ["blood_reg_no"], schema="keiba",
    )
    op.create_index(
        "ix_slope_training_training_date",
        "slope_training", ["training_date"], schema="keiba",
    )

    op.create_table(
        "wood_training",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("blood_reg_no", sa.String(10), nullable=False),
        sa.Column("training_date", sa.String(8), nullable=False),
        sa.Column("training_time", sa.String(4), nullable=True),
        sa.Column("center", sa.String(1), nullable=True),
        sa.Column("wood_course", sa.String(1), nullable=True),
        sa.Column("wood_direction", sa.String(1), nullable=True),
        sa.Column("time_10f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_2000_1800", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_9f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_1800_1600", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_8f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_1600_1400", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_7f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_1400_1200", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_6f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_1200_1000", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_5f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_1000_800", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_4f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_800_600", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_3f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_600_400", sa.Numeric(3, 1), nullable=True),
        sa.Column("time_2f", sa.Numeric(4, 1), nullable=True),
        sa.Column("lap_400_200", sa.Numeric(3, 1), nullable=True),
        sa.Column("lap_200_0", sa.Numeric(3, 1), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "blood_reg_no", "training_date", "training_time", "center",
            name="uq_wood_training_key",
        ),
        schema="keiba",
    )
    op.create_index(
        "ix_wood_training_blood_reg_no",
        "wood_training", ["blood_reg_no"], schema="keiba",
    )
    op.create_index(
        "ix_wood_training_training_date",
        "wood_training", ["training_date"], schema="keiba",
    )


def downgrade() -> None:
    op.drop_index("ix_wood_training_training_date", table_name="wood_training", schema="keiba")
    op.drop_index("ix_wood_training_blood_reg_no", table_name="wood_training", schema="keiba")
    op.drop_table("wood_training", schema="keiba")
    op.drop_index("ix_slope_training_training_date", table_name="slope_training", schema="keiba")
    op.drop_index("ix_slope_training_blood_reg_no", table_name="slope_training", schema="keiba")
    op.drop_table("slope_training", schema="keiba")
