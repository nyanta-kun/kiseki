"""POG のグループ・参加者・指名を keiba スキーマへ（テーブルのみ）

統合 Phase 5 の 5d-1。sekito.pog_group(22) / pog_user(1,401・22年分) の後継。
参照する側（API・画面）と引き継ぎスクリプトは別 PR。

移設元からの変更:

- `pog_group.user_ids`（**text にカンマ区切り**）→ `pog_group_members` へ正規化
- `pog_user.order`（SQL の予約語）→ `pick_order` へ改名
- `uid` は `sekito.users.id` を指していた → `keiba.users.id` への FK に変更
  （対応表は 2026-09-09 の事前登録で作成済み）
- 馬は `netkeiba_horse_id` のまま（**移行でキーが変わらないのが移植の前提**）。
  `keiba.pog_horses` への FK は張らない。ドラフト当日に馬マスタが
  追いついていない馬を指名できるようにするため（移設元も張っていない）

Revision ID: 202609091824_jra
Revises: 202609081739_jra
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202609091824_jra"
down_revision = "202609081739_jra"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pog_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False,
                  comment="POG の年度。1年 = 1グループ"),
        sa.Column("name", sa.Text(), nullable=True, comment="グループ名（例: 赤兎）"),
        sa.Column("notification_platform", sa.String(length=20), nullable=True,
                  comment="discord / line。2026 年度は discord のみ"),
        sa.Column("discord_webhook_url", sa.String(length=500), nullable=True),
        sa.Column("discord_enabled", sa.Boolean(), server_default=sa.false(),
                  nullable=False),
        sa.Column("line_group_id", sa.String(length=255), nullable=True),
        sa.Column("line_enabled", sa.Boolean(), server_default=sa.false(),
                  nullable=False),
        sa.Column("notification_settings", postgresql.JSONB(), nullable=True,
                  comment="entry / barrier / result / summary の各通知の on-off"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("year", name="uq_pog_groups_year"),
        schema="keiba",
    )
    op.create_table(
        "pog_group_members",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["keiba.pog_groups.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["keiba.users.id"]),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
        schema="keiba",
    )
    op.create_table(
        "pog_picks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("netkeiba_horse_id", sa.String(length=20), nullable=True,
                  comment="指名した馬。keiba.pog_horses と同じキー（空指名は NULL）"),
        sa.Column("pick_order", sa.Integer(), nullable=True,
                  comment="指名順。移設元の `order`（予約語だったので改名）"),
        sa.Column("draft_order", sa.Integer(), nullable=True, comment="ドラフトの順番"),
        sa.Column("visible", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["keiba.pog_groups.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["keiba.users.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema="keiba",
    )
    op.create_index("ix_pog_picks_group_user", "pog_picks", ["group_id", "user_id"],
                    schema="keiba")
    op.create_index("ix_pog_picks_horse", "pog_picks", ["netkeiba_horse_id"],
                    schema="keiba")


def downgrade() -> None:
    op.drop_index("ix_pog_picks_horse", table_name="pog_picks", schema="keiba")
    op.drop_index("ix_pog_picks_group_user", table_name="pog_picks", schema="keiba")
    op.drop_table("pog_picks", schema="keiba")
    op.drop_table("pog_group_members", schema="keiba")
    op.drop_table("pog_groups", schema="keiba")
