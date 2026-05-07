"""Change race_entries unique constraint from (race_id, horse_number) to (race_id, horse_id)

Revision ID: a1b2c3d4e5f6
Revises: z5a6b7c8d9e0
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str = "z5a6b7c8d9e0"


def upgrade() -> None:
    # Step 1: race_results.entry_id が削除予定のエントリを指している場合、
    # 同 (race_id, horse_id) グループの MIN(id) エントリに付け替える
    op.execute("""
        UPDATE keiba.race_results
        SET entry_id = min_entries.min_id
        FROM keiba.race_entries re
        JOIN (
            SELECT race_id, horse_id, MIN(id) AS min_id
            FROM keiba.race_entries
            GROUP BY race_id, horse_id
            HAVING COUNT(*) > 1
        ) AS min_entries
          ON re.race_id = min_entries.race_id
         AND re.horse_id = min_entries.horse_id
        WHERE keiba.race_results.entry_id = re.id
          AND re.id != min_entries.min_id
    """)

    # Step 2: 重複エントリを削除（(race_id, horse_id) ごとに MIN(id) を残す）
    op.execute("""
        DELETE FROM keiba.race_entries
        WHERE id NOT IN (
            SELECT MIN(id) FROM keiba.race_entries GROUP BY race_id, horse_id
        )
    """)

    # Step 3: 旧制約 (race_id, horse_number) を削除
    op.drop_constraint("uq_race_entry_horse_num", "race_entries", schema="keiba")

    # Step 4: 新制約 (race_id, horse_id) を追加
    op.create_unique_constraint(
        "uq_race_entry_horse_id",
        "race_entries",
        ["race_id", "horse_id"],
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_constraint("uq_race_entry_horse_id", "race_entries", schema="keiba")
    op.create_unique_constraint(
        "uq_race_entry_horse_num",
        "race_entries",
        ["race_id", "horse_number"],
        schema="keiba",
    )
