"""Add special_registrations table for TOKU (特別登録馬) data

Revision ID: z5a6b7c8d9e0
Revises: y4z5a6b7c8d9
Create Date: 2026-05-02
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "z5a6b7c8d9e0"
down_revision: str = "y4z5a6b7c8d9"


def upgrade() -> None:
    op.create_table(
        "special_registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jravan_race_id", sa.String(16), nullable=False, comment="JRA-VANレースID"),
        sa.Column("race_date", sa.String(8), nullable=False, comment="開催日 YYYYMMDD"),
        sa.Column("course_code", sa.String(2), nullable=False, comment="場コード"),
        sa.Column("race_number", sa.Integer(), nullable=False, comment="レース番号"),
        sa.Column("jravan_horse_code", sa.String(10), nullable=False, comment="血統登録番号"),
        sa.Column("horse_name", sa.String(100), nullable=False, comment="馬名"),
        sa.Column("sex", sa.String(4), nullable=True, comment="性別"),
        sa.Column("age", sa.Integer(), nullable=True, comment="馬齢"),
        sa.Column("east_west_code", sa.String(1), nullable=True, comment="東西所属コード"),
        sa.Column("jravan_trainer_code", sa.String(5), nullable=True, comment="調教師コード"),
        sa.Column("trainer_name", sa.String(50), nullable=True, comment="調教師名略称"),
        sa.Column("data_type", sa.String(1), nullable=True, comment="データ区分"),
        sa.Column("race_name", sa.String(200), nullable=True, comment="競走名"),
        sa.Column("grade_code", sa.String(1), nullable=True, comment="グレードコード"),
        sa.Column("distance", sa.Integer(), nullable=True, comment="距離（m）"),
        sa.Column("track_code", sa.String(2), nullable=True, comment="トラックコード"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("jravan_race_id", "jravan_horse_code", name="uq_special_reg_race_horse"),
        schema="keiba",
    )
    op.create_index("ix_special_reg_race_id", "special_registrations", ["jravan_race_id"], schema="keiba")
    op.create_index("ix_special_reg_date", "special_registrations", ["race_date"], schema="keiba")


def downgrade() -> None:
    op.drop_index("ix_special_reg_date", table_name="special_registrations", schema="keiba")
    op.drop_index("ix_special_reg_race_id", table_name="special_registrations", schema="keiba")
    op.drop_table("special_registrations", schema="keiba")
