"""keirin.submission_skips 新設と netkeirin_submissions.cancel_reason 追加

## なぜ

入稿を見送った理由が **どこにも残っていなかった**。
`netkeirin_submit_wt.py` の各ゲートは `continue` で抜けるだけで、
理由は VPS のログにしか無い（`data/logs/netkeirin_YYYY-MM-DD.log`）。

2026-08-25 松阪7R(7S) は平均払戻ゲート（想定平均 19,226円 <= 20,000円）で
入稿していないのに、Web の一覧と Discord では `picks_history`（ランクの候補）
由来で「購入・的中 42,400円」と表示されていた。表示を入稿へ揃えると
今度は「なぜ売らなかったのか」が画面から分からなくなるため、
**見送りを記録する器**をここで作る。

## なぜ status='skipped' を足さないか

`netkeirin_submissions` は「入稿した記録」で、`bet_detail` が
「何をいくらで買ったか」の唯一の正本。ここへ売っていない行を混ぜると
`_already_submitted()` と各所の status フィルタ（`/summary` `/sold-performance`
`/proposals`）すべてに波及し、**取りこぼすと売上集計へ静かに混入する**。
別テーブルなら既存の集計を1行も変えずに足せる。

## 一意性

`(race_key, rank_key, session)` で UPSERT する。同じ波を2回流しても増えず、
波が変われば別行として残る（朝は見送り→夕方に入稿、が追える）。
表示側は `decided_at` の新しい1件を使う。

Revision ID: 202608252140_keirin
Revises: 202608230945_keirin
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608252140_keirin"
down_revision = "202608230945_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "submission_skips"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # 一覧は日付で引くので、race_key から切り出さず列で持つ
        sa.Column("race_date", sa.Date, nullable=False),
        # 例 '20260825_47_07'（ランク接尾辞なし＝wt_races.race_key と同じ形）
        sa.Column("race_key", sa.String(32), nullable=False),
        # 例 '7S'（netkeirin_submissions.rank_key と同じ形）
        sa.Column("rank_key", sa.String(16), nullable=False),
        sa.Column("session", sa.String(16), nullable=False),
        # 語彙の正本は backend/src/services/keirin_skip_reasons.py
        sa.Column("reason_code", sa.String(32), nullable=False),
        # 実測値つきの文言（例 '平均払戻 19,226円 <= 20,000円'）
        sa.Column("reason_text", sa.Text, nullable=True),
        sa.Column("decided_at", sa.DateTime, nullable=False,
                  server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index(
        f"uq_{TABLE}_race_rank_session", TABLE,
        ["race_key", "rank_key", "session"], unique=True, schema=SCHEMA,
    )
    op.create_index(f"ix_{TABLE}_race_date", TABLE, ["race_date"], schema=SCHEMA)

    # 取消の理由。人が押すボタンごとに何を選んだかを残す
    # （安い配当の一括取消 / 場単位 / 個別 / 強制）。
    op.add_column(
        "netkeirin_submissions",
        sa.Column("cancel_reason", sa.String(255), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("netkeirin_submissions", "cancel_reason", schema=SCHEMA)
    op.drop_index(f"ix_{TABLE}_race_date", TABLE, schema=SCHEMA)
    op.drop_index(f"uq_{TABLE}_race_rank_session", TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
