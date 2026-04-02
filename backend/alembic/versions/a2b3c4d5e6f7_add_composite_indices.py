"""add composite indices to calculated_indices and odds_history

Revision ID: a2b3c4d5e6f7
Revises: f6a7b8c9d0e1
Create Date: 2026-04-02

PF-003 / PF-004: クエリパフォーマンス改善のため複合インデックスを追加する。

追加インデックス:
  1. calculated_indices (race_id, version)
     - バージョン指定でのレース全馬取得クエリを高速化
  2. calculated_indices (race_id, horse_id, version)
     - 特定馬・バージョンの指数取得クエリを高速化（カバリングインデックス）
  3. odds_history (race_id, bet_type, fetched_at)
     - リアルタイムオッズ取得・時系列クエリを高速化
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "keiba"


def upgrade() -> None:
    # 1. calculated_indices: (race_id, version) 複合インデックス
    op.create_index(
        "ix_calculated_indices_race_id_version",
        "calculated_indices",
        ["race_id", "version"],
        schema=SCHEMA,
    )

    # 2. calculated_indices: (race_id, horse_id, version) 複合インデックス
    op.create_index(
        "ix_calculated_indices_race_horse_version",
        "calculated_indices",
        ["race_id", "horse_id", "version"],
        schema=SCHEMA,
    )

    # 3. odds_history: (race_id, bet_type, fetched_at) 複合インデックス
    op.create_index(
        "ix_odds_history_race_bet_type_fetched_at",
        "odds_history",
        ["race_id", "bet_type", "fetched_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_odds_history_race_bet_type_fetched_at",
        table_name="odds_history",
        schema=SCHEMA,
    )

    op.drop_index(
        "ix_calculated_indices_race_horse_version",
        table_name="calculated_indices",
        schema=SCHEMA,
    )

    op.drop_index(
        "ix_calculated_indices_race_id_version",
        table_name="calculated_indices",
        schema=SCHEMA,
    )
