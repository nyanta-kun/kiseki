"""POG 通知の送信記録 keiba.pog_notifications（テーブルのみ）

統合 Phase 5 の 5f-1。sekito.pog_notifications(323行) の後継。
参照する側（通知ジョブ）は別 PR。

🔴 一意制約を DB 側に置く。移設元は「SELECT で送信済みか見る → 送る → INSERT」で、
`pog-result` は **10分ごと**に走るため前の実行が長引くと二度送りうる。

素の UNIQUE は NULL 同士を別物として扱うので、course_code などが NULL の
entry / barrier を弾けない。COALESCE を使った一意インデックスにする
（`UNIQUE NULLS NOT DISTINCT` は PostgreSQL 15 以降で、**手元の検証環境 14.17 では
構文エラーになる**＝本番でしか試せなくなる）。

Revision ID: 202609092313_jra
Revises: 202609092204_jra
Create Date: 2026-09-09
"""

import sqlalchemy as sa

from alembic import op

revision = "202609092313_jra"
down_revision = "202609092204_jra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pog_notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("notification_type", sa.String(length=20), nullable=False,
                  comment="result / entry / barrier / summary"),
        sa.Column("race_date", sa.Date(), nullable=False),
        sa.Column("course_code", sa.String(length=8), nullable=True,
                  comment="result のみ。entry / barrier は NULL"),
        sa.Column("race_no", sa.Integer(), nullable=True),
        sa.Column("horse_name", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True,
                  comment="送った内容の要約。人が後から読んで分かるため"),
        sa.Column("sent_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["keiba.pog_groups.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="keiba",
    )
    op.create_index("ix_pog_notifications_lookup", "pog_notifications",
                    ["group_id", "notification_type", "race_date"], schema="keiba")
    # 🔴 NULL 混じりの重複を弾く。
    #
    # 素の UNIQUE は NULL 同士を「別物」として扱うので、course_code / race_no /
    # horse_name が NULL の entry / barrier を何度でも入れられてしまう。
    #
    # PostgreSQL 15 以降なら `UNIQUE NULLS NOT DISTINCT` が使えるが、**COALESCE を
    # 使った一意インデックス**にする。本番は 16.15 でも手元の検証環境は 14.17 で、
    # バージョン依存の構文だと**本番でしか試せない**（実際 15 未満で構文エラーになった）。
    op.execute(
        """
        CREATE UNIQUE INDEX uq_pog_notifications_dedup
        ON keiba.pog_notifications (
            group_id, notification_type, race_date,
            COALESCE(course_code, ''), COALESCE(race_no, -1), COALESCE(horse_name, '')
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_pog_notifications_lookup", table_name="pog_notifications",
                  schema="keiba")
    op.drop_table("pog_notifications", schema="keiba")
