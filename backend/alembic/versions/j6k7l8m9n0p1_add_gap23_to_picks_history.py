"""add gap23 column to keirin picks_history (正式化)

gap23: 指数2位と3位の予測確率差。**pt（パーセントポイント）スケール**で格納する
（gap12/gap34 は 0-1 スケール。gap23 のみ notify_prerace_wt._calc_gap23 が
×100 済みの値を書くという歴史的経緯があり、既存データとの互換のため踏襲）。
SS（7PLUS_R）判定条件 gap23>=1pt に使用する。

この列は本番 PostgreSQL には手動 ALTER で先行追加されていた「幽霊カラム」
（alembic にも keirin リポジトリの migrate_db にも DDL が無かった）。
本 migration はそれを履歴に正式化するもので、既存環境では IF NOT EXISTS
により no-op、新規環境では列を作成する（2026-07-12 レビュー是正）。

Revision ID: j6k7l8m9n0p1
Revises: i5j6k7l8m9n0
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op

revision = "j6k7l8m9n0p1"
down_revision = "i5j6k7l8m9n0"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    # 本番には手動追加済みのため IF NOT EXISTS（op.add_column は既存列でエラーになる）
    op.execute(
        f"ALTER TABLE {SCHEMA}.picks_history ADD COLUMN IF NOT EXISTS gap23 NUMERIC(8, 4)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.picks_history DROP COLUMN IF EXISTS gap23")
