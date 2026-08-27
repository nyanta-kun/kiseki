"""keirin.type_lab_picks 新設 — 型ラボの検証を既存と混ぜずに貯める器

## なぜ別テーブルか

型ラボ（`keirin/src/type_lab.py`）は**既存商品の全面置き換えを想定した設計**で、
まず過去のペーパー検証と1週間程度の実地検証を行う段階にある。
`picks_history` や `netkeirin_submissions` へ混ぜると

  - 既存の一覧・統計・売上集計（`/picks` `/stats` `/sold-performance` `/summary`）へ
    静かに混入する（`keirin_sold_source_of_truth_2026_08_25` で一度直した型）
  - 入稿の重複判定 `_already_submitted()` が誤作動する

ので**新しいテーブルへ隔離する**。既存のクエリを1行も変えずに足せる。

## 一意性

`(race_key, plan_key, mode)`。同じ日を2回流しても増えず、
ペーパー(`paper`)と実地(`live`)は同じレースで並存する（突き合わせに要る）。

## 何を持つか

- **型の根拠**（type_label / axis_sum / arare / gap）を行に埋める。
  後から `wt_entries` を引き直しても型が再現できないと検証にならない
  （モデルが更新されると p3 が変わるため）
- **買い目そのもの**（legs）を JSONB で持つ。`netkeirin_submissions.bet_detail` と
  同じ思想で、「何をいくらで買ったか」の正本を1箇所にする
- **予測オッズと確率**も legs の中へ入れる。採点時に予測と確定を突き合わせるため
  （勝者の呪い＝的中目の 確定/予測 が中央 0.87 という観測がある）

Revision ID: 202608270930_keirin
Revises: 202608252140_keirin
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202608270930_keirin"
down_revision = "202608252140_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "type_lab_picks"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # ── レース ──
        sa.Column("race_key", sa.String(32), nullable=False),
        sa.Column("race_date", sa.Date, nullable=False),
        sa.Column("venue_name", sa.String(32), nullable=True),
        sa.Column("race_no", sa.Integer, nullable=True),
        sa.Column("race_type", sa.String(32), nullable=True),
        sa.Column("n_entries", sa.Integer, nullable=True),
        sa.Column("day_index", sa.Integer, nullable=True),
        # ── 型の根拠（後から再現できるように行へ埋める）──
        sa.Column("type_label", sa.String(1), nullable=False),
        sa.Column("axis_sum", sa.Numeric(6, 4), nullable=True),
        sa.Column("arare", sa.Integer, nullable=True),
        sa.Column("gap", sa.Numeric(6, 4), nullable=True),
        sa.Column("axis1", sa.Integer, nullable=True),
        sa.Column("axis2", sa.Integer, nullable=True),
        # ── 商品 ──
        # 'paper'（過去のペーパー検証・vintage 予測）/ 'live'（当日・本番モデル）
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("plan_key", sa.String(16), nullable=False),   # 'A_hit' など
        sa.Column("bet_type", sa.String(16), nullable=False),   # trifecta | trio
        sa.Column("n_legs", sa.Integer, nullable=False),
        sa.Column("budget", sa.Integer, nullable=False),
        # [{combo, stake, pred_odds, prob}] — 何をいくらで買ったかの正本
        sa.Column("legs", postgresql.JSONB, nullable=False),
        sa.Column("pred_mean_payout", sa.Numeric(12, 1), nullable=True),
        sa.Column("pred_min_payout", sa.Numeric(12, 1), nullable=True),
        sa.Column("rule_version", sa.String(16), nullable=False),
        sa.Column("generated_at", sa.DateTime, nullable=False,
                  server_default=sa.text("now()")),
        # ── 結果（採点で埋める）──
        sa.Column("settled_at", sa.DateTime, nullable=True),
        sa.Column("win_combo", sa.String(16), nullable=True),
        sa.Column("hit", sa.Boolean, nullable=True),
        sa.Column("payout", sa.Integer, nullable=True),
        sa.Column("final_odds", sa.Numeric(10, 2), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(f"uq_{TABLE}_race_plan_mode", TABLE,
                    ["race_key", "plan_key", "mode"], unique=True, schema=SCHEMA)
    op.create_index(f"ix_{TABLE}_date_mode", TABLE, ["race_date", "mode"], schema=SCHEMA)
    op.create_index(f"ix_{TABLE}_type", TABLE, ["type_label"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_type", TABLE, schema=SCHEMA)
    op.drop_index(f"ix_{TABLE}_date_mode", TABLE, schema=SCHEMA)
    op.drop_index(f"uq_{TABLE}_race_plan_mode", TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
