"""extend_racecourse_features

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-25

racecourse_features にコーナー特性カラムを追加し、データを更新する。

追加カラム:
  corner_tightness   NUMERIC(3,2)  コーナーのきつさ (0.0=緩い〜1.0=急)
  start_to_corner_m  INTEGER       スタート〜第1コーナーの代表距離(m)

corner_tightness の根拠:
  - 1周距離・コーナー半径・スパイラルカーブ有無から推定
  - 函館(スパイラルカーブ・最短直線)=0.85, 東京(大回り)=0.30 等

start_to_corner_m の根拠:
  - 各競馬場の標準的なスタート地点から第1コーナーまでの距離
  - 短いほど多頭数時に外枠が不利、逃げ馬が位置取り争いを強いられる
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "keiba"

# (course_code, corner_tightness, start_to_corner_m)
CORNER_DATA = [
    ("01", 0.70, 130),   # 札幌: 小回り・円形に近い
    ("02", 0.85, 80),    # 函館: スパイラルカーブ・最もきつい
    ("03", 0.75, 100),   # 福島: 小回り
    ("04", 0.25, 350),   # 新潟: 日本最大・最も緩やか
    ("05", 0.30, 300),   # 東京: 大回り・余裕あり
    ("06", 0.65, 150),   # 中山: 中程度・急坂複合
    ("07", 0.35, 250),   # 中京: 大回り（改修後）
    ("08", 0.40, 200),   # 京都: 外回り基準
    ("09", 0.45, 200),   # 阪神: 外回り基準
    ("10", 0.70, 90),    # 小倉: 小回り
]


def upgrade() -> None:
    op.add_column(
        "racecourse_features",
        sa.Column("corner_tightness", sa.Numeric(3, 2), nullable=True,
                  comment="コーナーのきつさ (0.0=緩い〜1.0=急)"),
        schema=SCHEMA,
    )
    op.add_column(
        "racecourse_features",
        sa.Column("start_to_corner_m", sa.Integer, nullable=True,
                  comment="スタート〜第1コーナー代表距離(m)"),
        schema=SCHEMA,
    )

    conn = op.get_bind()
    for code, tightness, start_dist in CORNER_DATA:
        conn.execute(
            sa.text(
                f"UPDATE {SCHEMA}.racecourse_features "
                f"SET corner_tightness = :t, start_to_corner_m = :s "
                f"WHERE course_code = :c"
            ),
            {"t": tightness, "s": start_dist, "c": code},
        )


def downgrade() -> None:
    op.drop_column("racecourse_features", "corner_tightness", schema=SCHEMA)
    op.drop_column("racecourse_features", "start_to_corner_m", schema=SCHEMA)
