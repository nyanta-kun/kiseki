"""add keirin.wt_entries.pred_top2_pct（2着内率＝lgbm_wt_top2 の予測確率）

Web の出走表に **2着内率** を出すための列。既存の `pred_win_pct`（1着率＝
lgbm_wt_win）/ `pred_top3_pct`（3着内率＝配信 top3 モデル）と同じ経路で、
`wave-picks-wt` 生成時に全出走選手分が書き込まれる。

⚠️ **値が入るのはこの migration 以降に算出したレースだけ**。過去分は NULL の
   まま（表示は「—」）。バックフィルするなら当時の vintage モデルで行うこと。
   本番モデル（全期間 full-refit）をそのまま過去へ適用すると in-sample になる。

学習ターゲット `top2_flag` は 2026-08-09 に追加済み（`feature_wt.py`）。
DNF/失格（finish_order=0）は 2着内に含めない（win_flag と同じ扱い）。

Revision ID: 202608120700_keirin
Revises: 202608111930_keirin
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608120700_keirin"
down_revision = "202608111930_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column(
        "wt_entries",
        sa.Column("pred_top2_pct", sa.Numeric(5, 1), nullable=True,
                  comment="2着内率(%)＝lgbm_wt_top2 の予測確率"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("wt_entries", "pred_top2_pct", schema=SCHEMA)
