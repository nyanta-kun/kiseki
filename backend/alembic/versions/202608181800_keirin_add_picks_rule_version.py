"""add keirin.picks_history.rule_version（判定ルールの版）

`picks_history` は日次では**当月しか再構築しない**
（`reconcile_walkforward_tail.sh` は `--tail-only`）。過去月は書かれた当時の
コードのまま残るので、閾値や買い方を変えると台帳が静かに世代混在する。

🔴 実害が出ている。2026-08-18 の 7S 閾値調査で「axis_sum 1.40〜1.50 の帯は
   どのランクも出していない」と判定したが、参照した RANK_7S 行の一部は
   **7S の上限が 1.50 だった時代**のもので当然その帯を含んでいた。
   混在は例外を出さないため気付けない。

この列は混在を**検出可能**にするためのもの。値は
`strategy_wt.rank_rule_version()` がランクの判定定数から自動導出する
（手で採番すると必ず上げ忘れる）。

⚠️ **既存行は NULL のまま**にする。遡って埋めると「その版で出した」という
   嘘になる。NULL = 「2026-08-18 以前・世代不明」。
⚠️ この列はルール変更の効果測定の**代わりにはならない**。効果測定は
   同じスクリプトを条件だけ変えて2回回すこと。

Revision ID: 202608181800_keirin
Revises: 202608181200_keirin
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608181800_keirin"
down_revision = "202608181200_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "picks_history"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("rule_version", sa.Text(), nullable=True,
                  comment="判定ルールの版（strategy_wt.rank_rule_version）。"
                          "NULL は 2026-08-18 以前で世代不明"),
        schema=SCHEMA,
    )
    op.create_index(f"ix_{SCHEMA}_{TABLE}_rule_version", TABLE,
                    ["rank", "rule_version"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index(f"ix_{SCHEMA}_{TABLE}_rule_version", table_name=TABLE,
                  schema=SCHEMA)
    op.drop_column(TABLE, "rule_version", schema=SCHEMA)
