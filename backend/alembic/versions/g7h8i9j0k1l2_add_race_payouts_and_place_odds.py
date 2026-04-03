"""add race_payouts table and place_odds to race_results

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7h8i9j0k1l2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # race_results に place_odds カラム追加（複勝確定倍率）
    op.add_column(
        "race_results",
        sa.Column(
            "place_odds",
            sa.Numeric(6, 1),
            nullable=True,
            comment="複勝確定払戻倍率（HR レコードから取得、100円あたり払戻÷100）",
        ),
        schema="keiba",
    )

    # race_payouts テーブル新規作成
    op.create_table(
        "race_payouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "race_id",
            sa.Integer(),
            sa.ForeignKey("keiba.races.id"),
            nullable=True,
            comment="races テーブルの id",
        ),
        sa.Column(
            "bet_type",
            sa.String(20),
            nullable=False,
            comment="馬券種別（win/place/bracket/quinella/wide/exacta/trio/trifecta）",
        ),
        sa.Column(
            "combination",
            sa.String(30),
            nullable=False,
            comment="馬番組み合わせ（単: '3', 2頭: '3-7', 3頭: '3-7-11' など）",
        ),
        sa.Column(
            "payout",
            sa.Integer(),
            nullable=False,
            comment="払戻金額（100円あたり）",
        ),
        sa.Column(
            "popularity",
            sa.Integer(),
            nullable=True,
            comment="人気順位",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("race_id", "bet_type", "combination", name="uq_race_payouts_race_type_combo"),
        schema="keiba",
    )
    op.create_index(
        "ix_race_payouts_race_id",
        "race_payouts",
        ["race_id"],
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_index("ix_race_payouts_race_id", table_name="race_payouts", schema="keiba")
    op.drop_table("race_payouts", schema="keiba")
    op.drop_column("race_results", "place_odds", schema="keiba")
