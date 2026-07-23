"""add keirin.netkeirin_submissions table

netkeirin（ウマい車券）自動下書き入稿の送信済み記録。race_key単位で1回だけ
入稿するための重複防止と、朝夕バッチ完了後のDiscordサマリー集計に使う。

Revision ID: r1s2t3u4v5w6
Revises: n0p1q2r3s4t5
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r1s2t3u4v5w6"
down_revision = "n0p1q2r3s4t5"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.create_table(
        "netkeirin_submissions",
        sa.Column("race_key", sa.String(35), primary_key=True),
        sa.Column("submitted_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("session", sa.String(10)),
        sa.Column("venue_name", sa.String(20)),
        sa.Column("race_no", sa.Integer()),
        sa.Column("gate_label", sa.String(10)),
        sa.Column("axis1", sa.Integer()),
        sa.Column("axis2", sa.Integer()),
        sa.Column("netkeirin_race_id", sa.String(20)),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("netkeirin_submissions", schema=SCHEMA)
