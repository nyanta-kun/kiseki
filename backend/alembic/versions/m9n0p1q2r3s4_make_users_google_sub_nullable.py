"""make users.google_sub nullable for pre-registered (not-yet-logged-in) users

事前登録ユーザー（管理画面でメールアドレスのみ登録し、まだ一度もログインして
いないユーザー）は初回ログイン時まで google_sub が確定しないため NULL を許容する。
初回ログイン時に upsert 処理で google_sub を埋める。

Revision ID: m9n0p1q2r3s4
Revises: l8m9n0p1q2r3
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m9n0p1q2r3s4"
down_revision = "l8m9n0p1q2r3"
branch_labels = None
depends_on = None

SCHEMA = "keiba"


def upgrade() -> None:
    op.alter_column("users", "google_sub", nullable=True, schema=SCHEMA)


def downgrade() -> None:
    op.alter_column("users", "google_sub", nullable=False, schema=SCHEMA)
