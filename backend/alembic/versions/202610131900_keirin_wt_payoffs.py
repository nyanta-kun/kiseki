"""keirin.wt_payoffs 新設 — 公式払戻（100円あたり）を当たり目ごとに持つ

## なぜ要るか（2026-10-13 小倉5R で判明）

採点はこれまで **発売締切時の最終オッズ（`wt_odds.odds_value`）× 賭け金**で払戻を
作っていた。同着でない決着ならこれは公式払戻と一致するが、**同着では一致しない**。
同着は売上を当たり目の間で分けて払うので、公式払戻はオッズの約半分になる:

    20261013_81_05（3着 2番・3番 同着）
      三連単 1-5-2  最終オッズ 52.1倍  → 公式払戻 2,620円
      三連単 1-5-3  最終オッズ 118.9倍 → 公式払戻 5,890円

L_lead（2,500円×2点）は 427,500円で採点されていたが、実際は 212,750円。

ユーザー判断（2026-10-13）で、**同着に限らず全レースを公式払戻で採点する**。

## 出どころ

winticket のオッズページ `FETCH_KEIRIN_RACE_ODDS` の各目の `payoffUnitPrice`
（当たり目だけ非 0）と券種ごとの `*PayoffStatus`（1 = 払戻確定）。
`keirin/src/official_payoffs.py` が取り込む。

## なぜ `wt_odds` の列にしないか

`wt_odds` は 3,400万行あり、取り込みは `INSERT OR REPLACE` の上書き。払戻は
**当たり目の数行だけ**で、オッズとは確定の時刻も違う（結果が出た直後は
`PayoffStatus=0`）。別テーブルにすれば「払戻がまだ無い」を行の有無で表せる。

⚠️ `keirin.wt_race_payouts` は名前に反して**公式払戻ではない**（全目に値があり
   999,990 で頭打ち＝オッズ×100 の写し）。しかも 2026-07-04 で止まっている。

Revision ID: 202610131900_keirin
Revises: 202610010607_shared
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202610131900_keirin"
down_revision = "202610010607_shared"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "wt_payoffs"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("race_key", sa.String(30), nullable=False),
        # 'trio' | 'trifecta'（wt_odds と同じ券種名）
        sa.Column("bet_type", sa.String(20), nullable=False),
        # wt_odds と同じ表記: 券種を問わず '-' 区切り・三連複は昇順
        sa.Column("combination", sa.String(20), nullable=False),
        # 100円あたりの公式払戻（円）
        sa.Column("payoff", sa.Integer, nullable=False),
        sa.Column("collected_at", sa.DateTime, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("race_key", "bet_type", "combination", name=f"pk_{TABLE}"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table(TABLE, schema=SCHEMA)
