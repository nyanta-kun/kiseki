"""ユーザーごとの表示メニュー（POG / 中央 / 地方 / 競輪）のフラグを足す

Revision ID: 202609110930_shared
Revises: 202609101530_jra
Create Date: 2026-09-10

## 何のための列か

管理者がユーザーごとに表示可能メニューを ON/OFF する（2026-09-10 ユーザー指示）。
判定の正本は `backend/src/services/menu_access.py`。この列は**保存値**でしかない。

## 🔴 既定値は「移行しても今日と見え方が変わらない」ように選んである

| 列 | 既定 | 理由 |
|---|---|---|
| `menu_jra` | true | 従来は全員に中央のナビが出ていた |
| `menu_chihou` | true | 同上 |
| `menu_keirin` | false | 従来から admin 限定（`proxy.ts`） |
| `menu_pog` | false | 従来は `pog_group_members` の参加実績で出していた |

`server_default` だけでは**既存行が既定のまま**になるので、この移行で
次の 2 つを流し込む:

1. **POG の現参加者**（`pog_group_members` に居る人）→ `menu_pog = true`
   （併せて規則どおり `menu_jra` / `menu_chihou` も true）
2. **admin** → 4 つとも true

⚠️ 1 を入れ忘れると、移行した瞬間に POG 参加者9人のナビから POG が消える。
実測でこの移行の対象は参加実績のある `user_id` だけで、退会者は居ない。

## downgrade

列を落とすだけ。可視性は `pog_group_members` ベースの旧判定へ戻る。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202609110930_shared"
down_revision: str | Sequence[str] | None = "202609101530_jra"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "menu_pog",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="POG を表示するか",
        ),
        schema="keiba",
    )
    op.add_column(
        "users",
        sa.Column(
            "menu_jra",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="中央競馬を表示するか",
        ),
        schema="keiba",
    )
    op.add_column(
        "users",
        sa.Column(
            "menu_chihou",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="地方競馬を表示するか",
        ),
        schema="keiba",
    )
    op.add_column(
        "users",
        sa.Column(
            "menu_keirin",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="競輪を表示するか",
        ),
        schema="keiba",
    )

    # 1. POG の現参加者へ POG を付ける（規則どおり中央・地方も立てる）。
    op.execute(
        """
        UPDATE keiba.users u
           SET menu_pog = true, menu_jra = true, menu_chihou = true
         WHERE EXISTS (
                 SELECT 1 FROM keiba.pog_group_members m WHERE m.user_id = u.id
               )
        """
    )
    # 2. admin は 4 つとも立てる（可視性は `resolve_menu_access` が role で
    #    素通しにするが、管理画面のチェックボックスを実態と揃えておく）。
    op.execute(
        """
        UPDATE keiba.users
           SET menu_pog = true, menu_jra = true,
               menu_chihou = true, menu_keirin = true
         WHERE role = 'admin'
        """
    )


def downgrade() -> None:
    op.drop_column("users", "menu_keirin", schema="keiba")
    op.drop_column("users", "menu_chihou", schema="keiba")
    op.drop_column("users", "menu_jra", schema="keiba")
    op.drop_column("users", "menu_pog", schema="keiba")
