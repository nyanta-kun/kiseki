"""add win5 tables

WIN5（重勝式）の WF レコードを受けるテーブルを新設する。

`race_payouts` に相乗りさせない理由は models.py のコメント参照
（単一レースに属さない / 人気順が無い / 行外の属性を持つ）。

レイアウトの出典: docs/sources/JV-Data4901.pdf「３０．重勝式(WIN5)」

Revision ID: 202609021945_shared
Revises: 202608310722_keirin
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202609021945_shared"
down_revision: str | Sequence[str] | None = "202608310722_keirin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "keiba"


def upgrade() -> None:
    op.create_table(
        "win5_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("held_date", sa.String(length=8), nullable=False,
                  comment="開催日 YYYYMMDD（WF 項番4+5）"),
        sa.Column("data_kubun", sa.String(length=1), nullable=True,
                  comment="WF 項番2。1:詳細発表 2:1R目確定 3:払戻発表 7:成績(月曜) 9:中止"),
        sa.Column("created_date", sa.String(length=8), nullable=True),
        sa.Column("sold_votes", sa.BigInteger(), nullable=True,
                  comment="WF 項番9 発売票数（票数であって金額ではない）"),
        sa.Column("refund_flag", sa.Boolean(), nullable=True),
        sa.Column("void_flag", sa.Boolean(), nullable=True),
        sa.Column("no_hit_flag", sa.Boolean(), nullable=True),
        sa.Column("carryover_start", sa.BigInteger(), nullable=True,
                  comment="WF 項番14 当日開始時のキャリーオーバー額（円）"),
        sa.Column("carryover_balance", sa.BigInteger(), nullable=True,
                  comment="WF 項番15 次回への繰越額（円）"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("held_date", name="uq_win5_events_held_date"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_keiba_win5_events_held_date"), "win5_events",
                    ["held_date"], unique=False, schema=SCHEMA)

    op.create_table(
        "win5_legs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("win5_event_id", sa.Integer(), nullable=False),
        sa.Column("leg_no", sa.Integer(), nullable=False, comment="1〜5"),
        sa.Column("jravan_race_id", sa.String(length=16), nullable=False,
                  comment="開催年月日(8)+場(2)+回(2)+日目(2)+R(2)"),
        sa.Column("race_id", sa.Integer(), nullable=True,
                  comment="解決できた場合のみ。未解決は NULL のまま件数を報告する"),
        sa.Column("valid_votes", sa.BigInteger(), nullable=True,
                  comment="WF 項番10 有効票数"),
        sa.ForeignKeyConstraint(["win5_event_id"], [f"{SCHEMA}.win5_events.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["race_id"], [f"{SCHEMA}.races.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("win5_event_id", "leg_no", name="uq_win5_legs_event_leg"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_keiba_win5_legs_win5_event_id"), "win5_legs",
                    ["win5_event_id"], unique=False, schema=SCHEMA)
    op.create_index(op.f("ix_keiba_win5_legs_jravan_race_id"), "win5_legs",
                    ["jravan_race_id"], unique=False, schema=SCHEMA)
    op.create_index(op.f("ix_keiba_win5_legs_race_id"), "win5_legs",
                    ["race_id"], unique=False, schema=SCHEMA)

    op.create_table(
        "win5_payouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("win5_event_id", sa.Integer(), nullable=False),
        sa.Column("combination", sa.String(length=10), nullable=False,
                  comment="WF 項番16a 組番。5レース×馬番2桁の10桁"),
        sa.Column("payout", sa.BigInteger(), nullable=True,
                  comment="WF 項番16b 払戻金（100円あたり・円）"),
        sa.Column("hit_votes", sa.BigInteger(), nullable=True,
                  comment="WF 項番16c 的中票数"),
        sa.ForeignKeyConstraint(["win5_event_id"], [f"{SCHEMA}.win5_events.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("win5_event_id", "combination",
                            name="uq_win5_payouts_event_combo"),
        schema=SCHEMA,
    )
    op.create_index(op.f("ix_keiba_win5_payouts_win5_event_id"), "win5_payouts",
                    ["win5_event_id"], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("win5_payouts", schema=SCHEMA)
    op.drop_table("win5_legs", schema=SCHEMA)
    op.drop_table("win5_events", schema=SCHEMA)
