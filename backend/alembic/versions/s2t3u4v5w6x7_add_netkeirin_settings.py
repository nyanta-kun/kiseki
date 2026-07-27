"""add keirin.netkeirin_settings table + composite key on netkeirin_submissions

netkeirin（ウマい車券）自動入稿のランク別ON/OFF・タイトル/コメントテンプレート設定。
`rank_key` は表示ランク（S1/7SS/7S/7A/9SS/9S/9A）+ 全体ON/OFFを表す特殊行 `_global`。
7SS/7Sの初期テンプレートは、これまで netkeirin_submit_wt.py にハードコードされていた
_build_title()/_build_comment() の文言をそのまま移植し、既存の入稿内容を変えない。

あわせて netkeirin_submissions のPKを race_key 単独から (race_key, rank_key) の複合へ
張り替える。同一レースが複数ランク（例: S1と7A）で同時に選ばれる実例が本番にあり、
race_key単独PKだと後勝ちのランクが先勝ちの入稿記録を上書きしてしまうため。
既存行の rank_key は gate_label（SS/SS+→7SS, S→7S）から補完する（これまで実際に
入稿されていたのはS7=7SS/7Sのみのため、この対応で全既存行を復元できる）。

Revision ID: s2t3u4v5w6x7
Revises: r1s2t3u4v5w6
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "s2t3u4v5w6x7"
down_revision = "r1s2t3u4v5w6"
branch_labels = None
depends_on = None

SCHEMA = "keirin"

_DEFAULT_COMMENT = (
    "本日の二軸をお届けします。\n\n"
    "買い目は三連複・軸2車流し（5点均等）です。独自の検証では、この5点のうち"
    "最終オッズが低い（目安5〜10倍以下）組み合わせを購入対象から外すと、"
    "的中率は下がる一方で回収率は上昇する傾向を確認しています。"
    "二軸探偵の入稿は発走前の最終オッズを確認できないタイミングで行っているため、"
    "この絞り込みは行っておりません。\n\n"
    "レース直前の最終オッズをご自身でご確認いただき、低倍率の目を外すなど、"
    "回収率を意識したアレンジにもぜひご活用ください。"
)

# rank_key, enabled, title_template, comment_template
_SEED_ROWS = [
    ("_global", True, "", ""),
    ("S1", True, "{venue}{race_no}R 二軸探偵", _DEFAULT_COMMENT),
    ("7SS", True, "{venue}{race_no}R 二軸探偵", _DEFAULT_COMMENT),
    ("7S", True, "{venue}{race_no}R 二軸探偵", _DEFAULT_COMMENT),
    ("7A", True, "{venue}{race_no}R 二軸探偵", _DEFAULT_COMMENT),
    ("9SS", True, "{venue}{race_no}R 二軸探偵", _DEFAULT_COMMENT),
    ("9S", True, "{venue}{race_no}R 二軸探偵", _DEFAULT_COMMENT),
    ("9A", True, "{venue}{race_no}R 二軸探偵", _DEFAULT_COMMENT),
]


def upgrade() -> None:
    settings_table = op.create_table(
        "netkeirin_settings",
        sa.Column("rank_key", sa.String(10), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("title_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("comment_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.bulk_insert(
        settings_table,
        [
            {
                "rank_key": rank_key,
                "enabled": enabled,
                "title_template": title,
                "comment_template": comment,
            }
            for rank_key, enabled, title, comment in _SEED_ROWS
        ],
    )

    # netkeirin_submissions: race_key 単独PK → (race_key, rank_key) 複合PKへ
    op.add_column(
        "netkeirin_submissions",
        sa.Column("rank_key", sa.String(10), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.netkeirin_submissions
        SET rank_key = CASE
            WHEN gate_label IN ('SS', 'SS+') THEN '7SS'
            WHEN gate_label = 'S' THEN '7S'
            ELSE gate_label
        END
        WHERE rank_key IS NULL
        """
    )
    op.alter_column("netkeirin_submissions", "rank_key", nullable=False, schema=SCHEMA)
    op.drop_constraint("netkeirin_submissions_pkey", "netkeirin_submissions", schema=SCHEMA, type_="primary")
    op.create_primary_key(
        "netkeirin_submissions_pkey",
        "netkeirin_submissions",
        ["race_key", "rank_key"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("netkeirin_submissions_pkey", "netkeirin_submissions", schema=SCHEMA, type_="primary")
    op.create_primary_key(
        "netkeirin_submissions_pkey",
        "netkeirin_submissions",
        ["race_key"],
        schema=SCHEMA,
    )
    op.drop_column("netkeirin_submissions", "rank_key", schema=SCHEMA)
    op.drop_table("netkeirin_settings", schema=SCHEMA)
