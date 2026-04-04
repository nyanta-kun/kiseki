"""Add yoso tables: user_predictions, user_imports, user_display_settings, can_input_index

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-04-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "j0k1l2m3n4o5"
down_revision: str = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- User.can_input_index 追加 ---
    op.add_column(
        "users",
        sa.Column(
            "can_input_index",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="TARGET外部指数の投入権限フラグ",
        ),
        schema="keiba",
    )

    # --- user_predictions ---
    op.create_table(
        "user_predictions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("keiba.users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "race_id",
            sa.Integer(),
            sa.ForeignKey("keiba.races.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "horse_id",
            sa.Integer(),
            sa.ForeignKey("keiba.horses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mark",
            sa.String(4),
            nullable=True,
            comment="印（◎○▲△×）",
        ),
        sa.Column(
            "user_index",
            sa.Numeric(6, 2),
            nullable=True,
            comment="ユーザー投入指数（TARGET外部指数等）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "race_id", "horse_id", name="uq_user_predictions_key"),
        schema="keiba",
    )

    # --- user_imports ---
    op.create_table(
        "user_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("keiba.users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column(
            "race_date",
            sa.String(8),
            nullable=False,
            comment="YYYYMMDD",
        ),
        sa.Column(
            "total_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "saved_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "error_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="keiba",
    )

    # --- user_display_settings ---
    op.create_table(
        "user_display_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("keiba.users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            sa.ForeignKey("keiba.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "show_mark",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="他ユーザーの印を表示するか",
        ),
        sa.Column(
            "show_index",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="他ユーザーの指数を表示するか（相手のcan_input_indexも必要）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "owner_user_id", "target_user_id", name="uq_user_display_settings_key"
        ),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_table("user_display_settings", schema="keiba")
    op.drop_table("user_imports", schema="keiba")
    op.drop_table("user_predictions", schema="keiba")
    op.drop_column("users", "can_input_index", schema="keiba")
