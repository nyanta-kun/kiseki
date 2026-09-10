"""POG ドラフトのサイコロ結果の表と、指名の一意制約を足す

Revision ID: 202609101530_jra
Revises: 202609100730_shared
Create Date: 2026-09-10

## 何のための表か

ドラフトで**同じ馬を複数人が指名した**とき、サイコロ 3 個を振って勝者を
決める。その出目を残す（移設元 `sekito.pog_roll` の後継）。

    year + user_id + draft_order で一意。振り直しは UPSERT で上書きする。

## 🔴 `sum` を列として持つ

移設元と同じ。`die1 + die2 + die3` を保存する。計算で出せるが、
**同点判定の根拠が後から見えること**に意味があるので残す
（「あのとき誰が勝ったか」を年をまたいで参照する）。

## 移設元との違い

- `uid`（`sekito.users.id`）→ `user_id`（`keiba.users.id`）
- 出目に 1〜6 の検査を付けた。移設元は無検査で、クライアントが送った値を
  そのまま保存していた

## 🔴 `pog_picks` に一意制約を足す

ドラフトの指名は **1 人・1 巡につき 1 件**（移設元 `sekito.pog_user` の
主キーが `(group_id, uid, draft_order)` だった）。Phase 5a で写したとき
`id` を主キーにしたため、この一意性が落ちていた。

制約が無いと `ON CONFLICT` で上書きできず、**指名し直すたびに行が増える**。
実測（2026-09-10）で既存 1,401 行に重複は 0 件、`draft_order` の NULL も
0 件だったのでそのまま張れる。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202609101530_jra"
down_revision: str | Sequence[str] | None = "202609100730_shared"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 指名は 1 人・1 巡につき 1 件。ドラフトの上書き（ON CONFLICT）に要る。
    op.create_unique_constraint(
        "uq_pog_picks_group_user_order",
        "pog_picks",
        ["group_id", "user_id", "draft_order"],
        schema="keiba",
    )
    op.create_table(
        "pog_rolls",
        sa.Column("year", sa.SmallInteger(), nullable=False, comment="POG の年度"),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("keiba.users.id"),
            nullable=False,
            comment="振った人",
        ),
        sa.Column("draft_order", sa.Integer(), nullable=False, comment="ドラフトの何巡目か"),
        sa.Column("die1", sa.SmallInteger(), nullable=False),
        sa.Column("die2", sa.SmallInteger(), nullable=False),
        sa.Column("die3", sa.SmallInteger(), nullable=False),
        sa.Column("sum", sa.SmallInteger(), nullable=False, comment="die1+die2+die3"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("year", "user_id", "draft_order", name="pk_pog_rolls"),
        # ⚠️ 出目は 1〜6。移設元は無検査でクライアントの値をそのまま保存していた。
        sa.CheckConstraint("die1 BETWEEN 1 AND 6", name="ck_pog_rolls_die1"),
        sa.CheckConstraint("die2 BETWEEN 1 AND 6", name="ck_pog_rolls_die2"),
        sa.CheckConstraint("die3 BETWEEN 1 AND 6", name="ck_pog_rolls_die3"),
        sa.CheckConstraint('"sum" = die1 + die2 + die3', name="ck_pog_rolls_sum"),
        schema="keiba",
        comment="POG ドラフトの同着抽選（サイコロ3個）の出目",
    )
    op.create_index(
        "ix_pog_rolls_year_order", "pog_rolls", ["year", "draft_order"], schema="keiba"
    )


def downgrade() -> None:
    op.drop_index("ix_pog_rolls_year_order", table_name="pog_rolls", schema="keiba")
    op.drop_table("pog_rolls", schema="keiba")
    op.drop_constraint(
        "uq_pog_picks_group_user_order", "pog_picks", schema="keiba", type_="unique"
    )
