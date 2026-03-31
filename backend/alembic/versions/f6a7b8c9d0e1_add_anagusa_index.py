"""add anagusa_index to calculated_indices

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calculated_indices",
        sa.Column(
            "anagusa_index",
            sa.Numeric(5, 1),
            nullable=True,
            comment="穴ぐさ指数（sekito.anagusa ピック実績ベースの期待度スコア）",
        ),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("calculated_indices", "anagusa_index", schema="keiba")
