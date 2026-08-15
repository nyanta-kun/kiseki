"""add keirin.netkeirin_submissions.published_at（netkeirin で公開した時刻）

netkeirin の入稿は **入稿（＝下書きとして送る）** と **公開** が別操作になっている。
これまで kiseki 側は入稿までしか扱っておらず、公開は netkeirin の
`race_auth.html`（公開待ち一覧）で人が押していた。

2026-08-16 に確認画面（`/keirin/review`）から公開できるようにしたので、
「いつ公開したか」を記録する。`status` も `published` を取りうるようになる。

    proposed → submitted（＝netkeirin へ送信済み・公開待ち）→ published
                    ↘ deleted（論理削除）

🔴 **公開は不可逆**。netkeirin 自身の確認文言が「公開後は修正できなくなります」。
🔴 **公開済みは取消の対象から外す。** 公開済みに `action=delete` が効くかは
   netkeirin の仕様に記載が無く未確認で、含めると一括取消のたびに必ず失敗する行が
   混ざって明細が読めなくなる。

⚠️ 列を足すだけで既存行は NULL のまま。過去の入稿が公開済みかどうかは
   netkeirin 側にしか無く、遡って埋めることはできない
   （`action=get_wait` が返すのは**未公開**の一覧だけ）。

Revision ID: 202608160830_keirin
Revises: 202608150500_jra
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608160830_keirin"
down_revision = "202608150500_jra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "netkeirin_submissions",
        sa.Column("published_at", sa.DateTime(), nullable=True),
        schema="keirin",
    )


def downgrade() -> None:
    op.drop_column("netkeirin_submissions", "published_at", schema="keirin")
