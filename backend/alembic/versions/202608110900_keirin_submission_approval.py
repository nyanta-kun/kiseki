"""keirin の netkeirin 入稿に「承認制」と「取消」を入れる

2026-08-11。これまで入稿バッチ（7:00/13:00/18:00）は netkeirin へ**下書きを自動作成**し、
公開だけ人が netkeirin 上で行っていた。オッズ・推奨買い目・コメントを事前に確認したい
という要望で、**承認制**（入稿案を作る → 人が確認画面で承認 → はじめて netkeirin へ出す）
を追加する。

⚠️ **承認制は一時運用の想定**（問題が無ければ自動入稿へ戻す）。そのため
`netkeirin_settings._global.require_approval` の**フラグで切り替えられる**ようにし、
コード変更やデプロイ無しで元へ戻せるようにする。

## netkeirin_submissions の変更

| 列 | 用途 |
|---|---|
| `status` | `proposed`（入稿案・未送信）/ `submitted`（netkeirin へ送信済）/ `deleted`（取消） |
| `title` / `comment` | 入稿文面。確認画面での表示・編集に使う（従来は保存していなかった） |
| `netkeirin_item_id` | `action=delete` に必要な item_id（`race_auth.html` の削除ボタンから取得） |
| `proposed_at` / `approved_at` / `deleted_at` | 状態遷移の時刻 |

🔴 **既存行はすべて「送信済み」なので `status` の既定値は `submitted`**。
   ここを `proposed` にすると、過去の入稿がすべて未送信扱いになり
   `_already_submitted()` の二重入稿防止が壊れる。

🔴 **取消しても行は消さない（論理削除）**。`bet_detail` は
   「入稿時に何をいくらで買ったか」の**唯一の正本で後から再現できない**
   （202608071800_keirin 参照）。物理削除すると ROI・的中率の集計が壊れる。

## netkeirin_settings の変更

| 列 | 用途 |
|---|---|
| `require_approval` | 承認制の ON/OFF。`_global` 行の値だけを見る |

既定は `false`（＝従来どおり自動入稿）。**画面が出来るまでは false のまま**にし、
運用を始めるときに画面から true へ切り替える。

Revision ID: 202608110900_keirin
Revises: 202608071800_keirin
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608110900_keirin"
down_revision = "202608071800_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
SUBMISSIONS = "netkeirin_submissions"
SETTINGS = "netkeirin_settings"

STATUS_PROPOSED = "proposed"
STATUS_SUBMITTED = "submitted"
STATUS_DELETED = "deleted"


def upgrade() -> None:
    op.add_column(
        SUBMISSIONS,
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text(f"'{STATUS_SUBMITTED}'")),
        schema=SCHEMA,
    )
    # ⚠️ ループで足さず**1列ずつ書く**。tests/test_keirin_model_schema_sync.py は
    #    `op.add_column(<表>, sa.Column("<列>"...))` を AST で拾ってモデルとの
    #    取りこぼしを検査するが、ループ変数だと列名が解決できず**検査から漏れる**。
    op.add_column(SUBMISSIONS, sa.Column("title", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(SUBMISSIONS, sa.Column("comment", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(SUBMISSIONS, sa.Column("netkeirin_item_id", sa.Text(), nullable=True),
                  schema=SCHEMA)
    op.add_column(SUBMISSIONS, sa.Column("proposed_at", sa.DateTime(), nullable=True),
                  schema=SCHEMA)
    op.add_column(SUBMISSIONS, sa.Column("approved_at", sa.DateTime(), nullable=True),
                  schema=SCHEMA)
    op.add_column(SUBMISSIONS, sa.Column("deleted_at", sa.DateTime(), nullable=True),
                  schema=SCHEMA)

    # 確認画面は「その日の入稿案」を status で引く。日付は race_key の先頭8桁。
    op.create_index(
        f"ix_{SUBMISSIONS}_status", SUBMISSIONS, ["status"], schema=SCHEMA,
    )

    op.add_column(
        SETTINGS,
        sa.Column("require_approval", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(SETTINGS, "require_approval", schema=SCHEMA)
    op.drop_index(f"ix_{SUBMISSIONS}_status", table_name=SUBMISSIONS, schema=SCHEMA)
    for col in ("deleted_at", "approved_at", "proposed_at",
                "netkeirin_item_id", "comment", "title", "status"):
        op.drop_column(SUBMISSIONS, col, schema=SCHEMA)
