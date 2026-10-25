"""keiba.mekauchida_race_upset に発走前の札の列を足す — 発走後に判断済みの札へ差し替えるため

## なぜ要るか（2026-10-25 ユーザー判断）

kiseki は発走 1 時間前から 10 分おきにサイトを読み、発走前の札で `level` を
上書きしていた。サイトのページは約 10 分おきの更新なので、残るのは発走の
約 10〜20 分前の札で、サイトが発走直前に出す判断済みの札とは違う
（10/25 中央の発走済み 12R 中 7R で食い違いを実測）。

画面にはサイトが判断済みとして出した札を出したい。一方、発走前に読んだ札は
精度を測る記録として残したい。そこで:

- `level` / `site_updated` / `observed_at` = **画面に出す札**（発走後は判断済みの札）
- `level_prepost` / `prepost_site_updated` / `prepost_observed_at` = **発走前に最後に読んだ札**

## source の値

- `live`    : 発走前に読んだ札だけがある（まだ判断済みの札を読んでいない）
- `final`   : 発走後に判断済みの札を読んで `level` を差し替えた
- `history` : 履歴ページから後で取り込んだ札（確定オッズ時点）

🔴 発走前の精度を測るときは `level_prepost IS NOT NULL` で絞ること。
既存の `live` 行は、この移行で `level` を `level_prepost` へ写す。

Revision ID: 202610251700_shared
Revises: 202610251000_shared
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202610251700_shared"
down_revision = "202610251000_shared"
branch_labels = None
depends_on = None

SCHEMA = "keiba"
TABLE = "mekauchida_race_upset"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("level_prepost", sa.String(2), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("prepost_site_updated", sa.String(20), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("prepost_observed_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.execute(
        f"UPDATE {SCHEMA}.{TABLE} "
        "SET level_prepost = level, prepost_site_updated = site_updated, prepost_observed_at = observed_at "
        "WHERE source = 'live'"
    )


def downgrade() -> None:
    op.drop_column(TABLE, "prepost_observed_at", schema=SCHEMA)
    op.drop_column(TABLE, "prepost_site_updated", schema=SCHEMA)
    op.drop_column(TABLE, "level_prepost", schema=SCHEMA)
