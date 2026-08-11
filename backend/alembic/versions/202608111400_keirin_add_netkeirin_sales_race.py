"""add keirin.netkeirin_sales_race table

netkeirin「ウマい車券」予想家成績・売上ページ
（https://umaiaggre.yosoka.netkeiba.com/tool_keirin/result/yosoka_result.html）を
**list_detail=race**（レース別）で取得した結果を格納する。列は日別
（netkeirin_sales_daily）と同一で、集計IDだけが YYYYMMDD+場コード2桁+レース番号2桁
の12桁になる。

日別テーブルと分けているのは、レース別が「どのレースが売れたか・当たったか」という
別の粒度の事実であり、日別を再集計しても復元できないため。逆にレース別から日別を
合成することもしない（サイト側の日別行には販売の丸めや取消レースの扱いなど
レース別の合計と一致しない項目があるため、両方を一次資料として持つ）。

`race_key` は kiseki 側の keirin.wt_races / keirin.picks_history と結合するための
派生列（YYYYMMDD_VV_RR）。netkeirin の場コードは keirin.venue_info.venue_code と
同一体系であることを確認済み（2026-08-11）。

Revision ID: 202608111400_keirin
Revises: 202608110900_keirin
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608111400_keirin"
down_revision = "202608110900_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"

# 日別テーブルと共通の指標列。順序はサイトの表示順（＝スクレイパの _COLUMNS）と同じ。
_METRIC_COLUMNS = [
    ("n_predictions", sa.Integer(), "予想数"),
    ("n_predictions_staked", sa.Integer(), "予想数(賭け金あり)"),
    ("n_hits_incl_garami", sa.Integer(), "的中(ガミ含む)"),
    ("n_hits_excl_garami", sa.Integer(), "的中(ガミ除く)"),
    ("n_miss", sa.Integer(), "外れ"),
    ("stake_amount", sa.Integer(), "賭け金(円)"),
    ("payout_amount", sa.Integer(), "払戻金額(円)"),
    ("hit_rate_pct", sa.Float(), "的中率(%)"),
    ("recovery_rate_pct", sa.Float(), "回収率(%)"),
    ("n_sold", sa.Integer(), "販売個数"),
    ("sold_points", sa.Integer(), "販売pt"),
    ("sold_paid_points", sa.Integer(), "販売有償pt"),
    ("avg_sold_points", sa.Float(), "平均販売pt"),
    ("avg_sold_minutes", sa.Float(), "平均販売分(締切までの残り分数の平均)"),
    ("avg_sold_hour", sa.Float(), "平均販売時(締切までの残り時間・平均販売分の四捨五入)"),
    ("axis1_rate_1st", sa.Float(), "◎1着率(%)"),
    ("axis1_rate_2nd", sa.Float(), "◎2着率(%)"),
    ("axis1_rate_3rd", sa.Float(), "◎3着率(%)"),
    ("mark2_count", sa.Integer(), "〇件数"),
    ("mark2_rate_1st", sa.Float(), "〇1着率(%)"),
    ("mark2_rate_2nd", sa.Float(), "〇2着率(%)"),
    ("mark2_rate_3rd", sa.Float(), "〇3着率(%)"),
    ("mark3_count", sa.Integer(), "▲件数"),
    ("mark3_rate_1st", sa.Float(), "▲1着率(%)"),
    ("mark3_rate_2nd", sa.Float(), "▲2着率(%)"),
    ("mark3_rate_3rd", sa.Float(), "▲3着率(%)"),
    ("mark123_count", sa.Integer(), "◎〇▲件数"),
    ("transition_axis1_to_mark2_pct", sa.Float(), "◎→〇率(%)"),
    ("transition_axis1_to_mark3_pct", sa.Float(), "◎→▲率(%)"),
    ("transition_mark2_to_axis1_pct", sa.Float(), "〇→◎率(%)"),
    ("transition_mark3_to_axis1_pct", sa.Float(), "▲→◎率(%)"),
]


def upgrade() -> None:
    op.create_table(
        "netkeirin_sales_race",
        sa.Column("race_id", sa.String(12), primary_key=True,
                  comment="集計ID(YYYYMMDD+場コード2桁+レース番号2桁)"),
        sa.Column("race_date", sa.String(8), nullable=False, comment="開催日(YYYYMMDD)"),
        sa.Column("venue_code", sa.String(2), nullable=False,
                  comment="場コード(keirin.venue_info.venue_code と同一体系)"),
        sa.Column("race_no", sa.Integer(), nullable=False, comment="レース番号"),
        sa.Column("race_key", sa.String(20), nullable=False,
                  comment="kiseki側キー(YYYYMMDD_VV_RR)。wt_races/picks_history との結合用"),
        sa.Column("race_label", sa.String(120),
                  comment="集計名(例: 08/10 四日市 Ａ級 準決勝)"),
        *[sa.Column(name, type_, comment=comment) for name, type_, comment in _METRIC_COLUMNS],
        sa.Column("collected_at", sa.DateTime(), server_default=sa.func.now(), comment="収集日時"),
        schema=SCHEMA,
        comment="netkeirin『ウマい車券』予想家成績・売上（レース別）",
    )
    # 期間指定の抽出が主用途なので開催日に索引を張る。
    op.create_index(
        "ix_netkeirin_sales_race_date", "netkeirin_sales_race", ["race_date"], schema=SCHEMA
    )
    # picks_history / wt_races との結合キー。1レース1行だが UNIQUE にはしない
    #（万一 netkeirin 側の場コード体系が変わっても取込自体は落とさない）。
    op.create_index(
        "ix_netkeirin_sales_race_key", "netkeirin_sales_race", ["race_key"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_netkeirin_sales_race_key", table_name="netkeirin_sales_race", schema=SCHEMA)
    op.drop_index("ix_netkeirin_sales_race_date", table_name="netkeirin_sales_race", schema=SCHEMA)
    op.drop_table("netkeirin_sales_race", schema=SCHEMA)
