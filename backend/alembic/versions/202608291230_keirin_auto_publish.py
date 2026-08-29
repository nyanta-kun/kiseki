"""keirin.netkeirin_settings に「自動公開」の説明コメントを付け直す（2026-08-29）

## 何をするか

**列は増やさない。** 「自動公開」は既存の `require_approval` の**裏返し**として
表現する（2026-08-29・ユーザー要望「入稿確認を ON/OFF 切り替えにしたい」）。

| 画面 | ON の意味 |
|---|---|
| `/keirin/settings` の「自動公開」 | `require_approval = false`＝入稿データ作成と同時に netkeirin へ下書き入稿し、**そのまま公開まで**行う |
| `/admin` の「承認制」 | `require_approval = true`＝入稿案だけ作り、`/keirin/review` で承認するまで netkeirin へ出さない |

🔴 **列を足して2つのフラグにしない。** 「承認制」と「自動公開」は同じ1つの
   スイッチの裏表で、別々に持つと**両方 ON**（＝承認を待つのに公開する）という
   ありえない状態が作れてしまう。実際にそうなったとき、公開は不可逆なので
   取り返しがつかない。

したがってこの migration は列のコメントを実態へ合わせるだけ（データは触らない）。

Revision ID: 202608291230_keirin
Revises: 202608272200_keirin
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op

revision = "202608291230_keirin"
down_revision = "202608272200_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "netkeirin_settings"

NEW_COMMENT = (
    "承認制（_global 行のみ有効）。true = 入稿案だけ作り承認するまで netkeirin へ "
    "出さない / false = 入稿と同時に公開まで自動で行う（画面の「自動公開」ON）"
)
OLD_COMMENT = "承認制（_global 行のみ有効・ONだと承認するまで netkeirin へ出ない）"


def upgrade() -> None:
    op.execute(
        f'COMMENT ON COLUMN {SCHEMA}.{TABLE}.require_approval IS \'{NEW_COMMENT}\''
    )


def downgrade() -> None:
    op.execute(
        f'COMMENT ON COLUMN {SCHEMA}.{TABLE}.require_approval IS \'{OLD_COMMENT}\''
    )
