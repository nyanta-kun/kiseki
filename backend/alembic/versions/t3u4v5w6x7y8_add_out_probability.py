"""add out_probability to calculated_indices

着外率（6着以下確率）を保存する列を追加する。
Web の足切り（グレーアウト）判定を「総合指数のトップ差」から着外率へ置き換えるため。
検証: memory/jra_out_rate_3head_verification_2026_08_02.md

Revision ID: t3u4v5w6x7y8
Revises: s2t3u4v5w6x7
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "t3u4v5w6x7y8"
down_revision: str = "s2t3u4v5w6x7"
branch_labels = None
depends_on = None

SCHEMA = "keiba"


def upgrade() -> None:
    op.add_column(
        "calculated_indices",
        sa.Column(
            "out_probability",
            sa.Numeric(5, 4),
            nullable=True,
            comment="着外確率（6着以下・Web足切り判定用。models/jra_out_rate_lgb.txt）",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("calculated_indices", "out_probability", schema=SCHEMA)
