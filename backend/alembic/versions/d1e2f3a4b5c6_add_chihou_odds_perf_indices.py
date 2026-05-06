"""add performance indices to chihou.odds_history and chihou.calculated_indices

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b3
Create Date: 2026-05-07

DISTINCT ON クエリ高速化のため chihou スキーマに複合インデックスを追加する。

  1. chihou.odds_history (race_id, bet_type, combination, fetched_at DESC)
     - 最新オッズ取得の DISTINCT ON クエリで Index Only Scan が効くようになる
     - 現状は race_id 単一インデックスのみで全行スキャン後ソートが必要だった

  2. chihou.calculated_indices (race_id, version)
     - バージョン指定レース全馬取得クエリを高速化（keiba スキーマと同等の対応）
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "chihou"


def upgrade() -> None:
    # 1. odds_history: DISTINCT ON (race_id, combination) ORDER BY fetched_at DESC に最適化
    op.create_index(
        "ix_chihou_odds_history_race_type_combo_time",
        "odds_history",
        ["race_id", "bet_type", "combination", "fetched_at"],
        schema=SCHEMA,
        postgresql_ops={"fetched_at": "DESC"},
    )

    # 2. calculated_indices: (race_id, version) — スイートスポット推奨のバッチ取得用
    op.create_index(
        "ix_chihou_calc_idx_race_version",
        "calculated_indices",
        ["race_id", "version"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chihou_calc_idx_race_version",
        table_name="calculated_indices",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_chihou_odds_history_race_type_combo_time",
        table_name="odds_history",
        schema=SCHEMA,
    )
