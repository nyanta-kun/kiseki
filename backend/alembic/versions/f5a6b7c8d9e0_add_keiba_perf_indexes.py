"""add performance indexes to keiba.odds_history and keiba.calculated_indices

Revision ID: f5a6b7c8d9e0
Revises: b2c3d4e5f6a7
Create Date: 2026-05-13

DISTINCT ON クエリ高速化のため keiba スキーマに複合インデックスを追加する。
（chihou スキーマには d1e2f3a4b5c6 で追加済み。keiba への適用）

  1. keiba.odds_history (race_id, bet_type, combination, fetched_at DESC)
     - list_races の最新オッズ取得クエリで Index Only Scan が効くようになる
     - 変更前: race_id 単一インデックスのみで全行スキャン後ソートが必要だった
     - 開催日全レース(最大36R×18頭×履歴N件) → 最大36行に削減

  2. keiba.calculated_indices (race_id, version)
     - tuple_(race_id, version).in_(pairs) クエリを高速化
     - 変更前: race_id 単一インデックスで version はフィルタ後に評価
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "keiba"


def upgrade() -> None:
    conn = op.get_bind()

    # 1. odds_history: DISTINCT ON (race_id) ORDER BY fetched_at DESC に最適化
    exists = conn.execute(
        text(
            "SELECT 1 FROM pg_indexes WHERE schemaname=:s AND indexname=:n"
        ),
        {"s": SCHEMA, "n": "ix_keiba_odds_history_race_type_combo_time"},
    ).fetchone()
    if not exists:
        op.create_index(
            "ix_keiba_odds_history_race_type_combo_time",
            "odds_history",
            ["race_id", "bet_type", "combination", "fetched_at"],
            schema=SCHEMA,
            postgresql_ops={"fetched_at": "DESC"},
        )

    # 2. calculated_indices: (race_id, version) — list_races バッチ取得用
    exists2 = conn.execute(
        text(
            "SELECT 1 FROM pg_indexes WHERE schemaname=:s AND indexname=:n"
        ),
        {"s": SCHEMA, "n": "ix_keiba_calc_idx_race_version"},
    ).fetchone()
    if not exists2:
        op.create_index(
            "ix_keiba_calc_idx_race_version",
            "calculated_indices",
            ["race_id", "version"],
            schema=SCHEMA,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_keiba_calc_idx_race_version",
        table_name="calculated_indices",
        schema=SCHEMA,
        if_exists=True,
    )
    op.drop_index(
        "ix_keiba_odds_history_race_type_combo_time",
        table_name="odds_history",
        schema=SCHEMA,
        if_exists=True,
    )
