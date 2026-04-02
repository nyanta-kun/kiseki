"""add invitation system

Revision ID: b0c1d2e3f4a5
Revises: a8b9c0d1e2f3
Create Date: 2026-04-02

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: str | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 招待コードテーブル
    op.create_table(
        "invitation_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column(
            "grant_type",
            sa.String(20),
            nullable=False,
            comment="unlimited / weeks / date",
        ),
        sa.Column("weeks_count", sa.Integer(), nullable=True, comment="grant_type=weeks のとき"),
        sa.Column("target_date", sa.Date(), nullable=True, comment="grant_type=date のとき"),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_invitation_codes_code"),
        schema="keiba",
    )

    # ユーザーアクセス付与テーブル
    op.create_table(
        "user_access_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "grant_type",
            sa.String(20),
            nullable=False,
            comment="unlimited / weeks / date",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="NULL=無期限"),
        sa.Column(
            "source",
            sa.String(50),
            nullable=False,
            server_default="admin",
            comment="code / admin",
        ),
        sa.Column("source_code_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["keiba.users.id"],
            name="fk_user_access_grants_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_code_id"],
            ["keiba.invitation_codes.id"],
            name="fk_user_access_grants_source_code_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="keiba",
    )
    op.create_index(
        "ix_user_access_grants_user_id",
        "user_access_grants",
        ["user_id"],
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_index("ix_user_access_grants_user_id", table_name="user_access_grants", schema="keiba")
    op.drop_table("user_access_grants", schema="keiba")
    op.drop_table("invitation_codes", schema="keiba")
