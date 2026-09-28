"""地方オッズに「発表時刻」と「データ区分」を残す

Revision ID: 202609290614_shared
Revises: 202609200900_keirin
Create Date: 2026-09-29

## 何のための列か

`chihou.odds_history.fetched_at` は **API が受け取った時刻**でしかない。
UmaConn が古いスナップショットを返していても、取得ループが同じデータを
送り直していても、受け取った瞬間の時刻が入るので**鮮度表示は「最新」のまま**になる。

2026-09-29 にユーザーから「楽天競馬のリアルタイムオッズと乖離が大きい」と
報告があったが、遅れが UmaConn の配信側なのか取得ループ側なのかを
**DB からは切り分けられなかった**。O1 レコードには発表時刻（発表月日時分・
pos 28-35）とデータ区分（pos 3）が入っているのに、取込で捨てていたため。

| 列 | 中身 |
|---|---|
| `announced_at` | O1〜O6 の発表月日時分を **naive UTC** にしたもの（`fetched_at` と揃える）。初期値 `00000000` は NULL |
| `data_kubun` | 1:中間 2:前日売最終 3:最終 4:確定 5:確定(月曜) 9:レース中止 0:削除 |

`fetched_at - announced_at` が「発表から手元に届くまでの遅れ」になる。

## 既存行

NULL のまま。過去分は発表時刻を捨てているので復元できない。

## downgrade

列を落とすだけ。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202609290614_shared"
down_revision: str | Sequence[str] | None = "202609200900_keirin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL 許容・既定値なしの列追加は PostgreSQL ではカタログ更新だけで終わる
    # （テーブルの書き換えが起きない）ので、大きい odds_history でも即座に終わる。
    op.add_column(
        "odds_history",
        sa.Column(
            "announced_at",
            sa.DateTime(),
            nullable=True,
            comment="発表時刻（O レコードの発表月日時分・naive UTC）",
        ),
        schema="chihou",
    )
    op.add_column(
        "odds_history",
        sa.Column(
            "data_kubun",
            sa.String(1),
            nullable=True,
            comment="データ区分（1:中間 2:前日売最終 3:最終 4:確定 5:確定(月曜) 9:中止 0:削除）",
        ),
        schema="chihou",
    )


def downgrade() -> None:
    op.drop_column("odds_history", "data_kubun", schema="chihou")
    op.drop_column("odds_history", "announced_at", schema="chihou")
