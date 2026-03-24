"""add_post_time_to_races

Revision ID: a5567aa39d8b
Revises: 9ec7ac8184d1
Create Date: 2026-03-24 07:25:52.775598

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a5567aa39d8b"
down_revision: Union[str, None] = "9ec7ac8184d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "races",
        sa.Column("post_time", sa.String(4), nullable=True),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("races", "post_time", schema="keiba")
