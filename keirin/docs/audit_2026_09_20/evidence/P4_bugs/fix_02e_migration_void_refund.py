"""type_lab_picks.void_refund 新設 — 欠車返還ぶんを budget から分離して記録する

Revision ID: 202609200900_keirin
Revises: 202609120900_keirin
Create Date: 2026-09-20

## なぜ要るか

`keirin.type_lab_picks.legs` に**欠車（出走取消）の車番を含む leg**が混ざる
ことがある（買い目を組んだ後に出走取消が判明するケース）。そうした leg は
出走していない車番を含むので構造的に的中しえず、本来は「返還」——stake が
戻ってくる——のに、旧採点（`scripts/settle_type_lab_picks.py`）は単に
「外れた leg」として扱い、`budget`（投資額）の一部として全損計上していた
（監査実測 2026-09-20: live 2026-08-27〜09-19 の6,601行中 9行・6レースで
 計25,400円が返還対象なのに全損計上・詳細は
 `scratchpad/audit/P4_bugs/notes.md` item2）。

`budget` は「入稿時点の予算」表示にも使われているため、返還が判明した後も
書き換えない（生成時点の記録として残す）。代わりにこの列へ返還額を積み、
ROI 集計（`Σpayout / Σ(budget - void_refund)`）はここを差し引く側で対応する
（`backend/src/api/keirin_type_lab_router.py` 側の別修正とセット）。

🔴 **NULL ではなく 0 埋め**。`Σvoid_refund` をそのまま SUM してよいようにする
   （欠測を意識せず既存の集計 SQL に `- COALESCE(void_refund,0)` を足すだけで済む
   ようにするため、DEFAULT も 0 にしておく）。
"""
import sqlalchemy as sa

from alembic import op

revision = "202609200900_keirin"
down_revision = "202609120900_keirin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "type_lab_picks",
        sa.Column("void_refund", sa.Integer(), nullable=False,
                  server_default="0"),
        schema="keirin",
    )


def downgrade() -> None:
    op.drop_column("type_lab_picks", "void_refund", schema="keirin")
