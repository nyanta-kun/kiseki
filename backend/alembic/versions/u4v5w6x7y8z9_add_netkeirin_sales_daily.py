"""add keirin.netkeirin_sales_daily table

netkeirin「ウマい車券」予想家成績・売上ページ
（https://umaiaggre.yosoka.netkeiba.com/tool_keirin/result/yosoka_result.html、
list_detail=day）の日別スクレイピング結果を格納する。集計対象日はレース開催日
（サイト側の表記は「集計ID」=YYYYMMDD）。売上は速報値のため、収集の都度
UPSERTで上書きする想定（scripts/scrape_netkeirin_sales.py）。

Revision ID: u4v5w6x7y8z9
Revises: t3u4v5w6x7y8
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "u4v5w6x7y8z9"
down_revision = "t3u4v5w6x7y8"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.create_table(
        "netkeirin_sales_daily",
        sa.Column("sale_date", sa.String(8), primary_key=True, comment="集計ID(YYYYMMDD・開催日)"),
        sa.Column("n_predictions", sa.Integer()),
        sa.Column("n_predictions_staked", sa.Integer()),
        sa.Column("n_hits_incl_garami", sa.Integer()),
        sa.Column("n_hits_excl_garami", sa.Integer()),
        sa.Column("n_miss", sa.Integer()),
        sa.Column("stake_amount", sa.Integer()),
        sa.Column("payout_amount", sa.Integer()),
        sa.Column("hit_rate_pct", sa.Float()),
        sa.Column("recovery_rate_pct", sa.Float()),
        sa.Column("n_sold", sa.Integer()),
        sa.Column("sold_points", sa.Integer()),
        sa.Column("sold_paid_points", sa.Integer()),
        sa.Column("avg_sold_points", sa.Float()),
        sa.Column("avg_sold_minutes", sa.Float()),
        sa.Column("avg_sold_hour", sa.Float()),
        sa.Column("axis1_rate_1st", sa.Float()),
        sa.Column("axis1_rate_2nd", sa.Float()),
        sa.Column("axis1_rate_3rd", sa.Float()),
        sa.Column("mark2_count", sa.Integer()),
        sa.Column("mark2_rate_1st", sa.Float()),
        sa.Column("mark2_rate_2nd", sa.Float()),
        sa.Column("mark2_rate_3rd", sa.Float()),
        sa.Column("mark3_count", sa.Integer()),
        sa.Column("mark3_rate_1st", sa.Float()),
        sa.Column("mark3_rate_2nd", sa.Float()),
        sa.Column("mark3_rate_3rd", sa.Float()),
        sa.Column("mark123_count", sa.Integer()),
        sa.Column("transition_axis1_to_mark2_pct", sa.Float()),
        sa.Column("transition_axis1_to_mark3_pct", sa.Float()),
        sa.Column("transition_mark2_to_axis1_pct", sa.Float()),
        sa.Column("transition_mark3_to_axis1_pct", sa.Float()),
        sa.Column("collected_at", sa.DateTime(), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_keirin_netkeirin_sales_daily_date",
        "netkeirin_sales_daily",
        ["sale_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("netkeirin_sales_daily", schema=SCHEMA)
