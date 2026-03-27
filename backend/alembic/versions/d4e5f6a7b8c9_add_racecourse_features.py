"""add_racecourse_features

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-25

JRA全10競馬場のコース特徴マスタテーブルを追加し、シードデータを投入する。
コース適性指数の類似競馬場フォールバックに使用する。

データソース: JRA公式サイト・各競馬場紹介ページ（2026年3月時点）
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "keiba"

# JRA 10場のコース特徴シードデータ
# direction: 1=左回り, -1=右回り
# straight_distance: 最終直線距離(m) ※外回りがある場合は外回りを基準
# elevation_diff: 最終直線の高低差(m)
# circuit_length: 芝コース1周距離(m) ※外回り基準
# grass_type: '洋芝' / '野芝+洋芝'
SEED_DATA = [
    # code  name      direction  straight  elevation  circuit  grass
    ("01", "札幌",   -1,        266.1,    0.7,       1641,   "洋芝"),
    ("02", "函館",   -1,        262.1,    3.5,       1622,   "洋芝"),
    ("03", "福島",   -1,        292.0,    1.5,       1600,   "野芝+洋芝"),
    ("04", "新潟",    1,        658.7,    1.5,       2223,   "野芝+洋芝"),
    ("05", "東京",    1,        525.9,    2.0,       2083,   "野芝+洋芝"),
    ("06", "中山",   -1,        310.0,    2.2,       1840,   "野芝+洋芝"),
    ("07", "中京",    1,        412.5,    2.0,       1706,   "野芝+洋芝"),
    ("08", "京都",    1,        403.7,    3.1,       1894,   "野芝+洋芝"),
    ("09", "阪神",    1,        476.3,    1.8,       2089,   "野芝+洋芝"),
    ("10", "小倉",   -1,        293.0,    1.0,       1615,   "野芝+洋芝"),
]


def upgrade() -> None:
    op.create_table(
        "racecourse_features",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("course_code", sa.String(2), nullable=False, unique=True,
                  comment="場コード（races.course と対応）"),
        sa.Column("course_name", sa.String(20), nullable=False,
                  comment="競馬場名"),
        sa.Column("direction", sa.SmallInteger, nullable=False,
                  comment="回り方向: 1=左回り, -1=右回り"),
        sa.Column("straight_distance", sa.Numeric(6, 1), nullable=False,
                  comment="最終直線距離(m)"),
        sa.Column("elevation_diff", sa.Numeric(4, 2), nullable=False,
                  comment="最終直線高低差(m)"),
        sa.Column("circuit_length", sa.Integer, nullable=False,
                  comment="芝コース1周距離(m)"),
        sa.Column("grass_type", sa.String(20), nullable=False,
                  comment="芝種別: 洋芝 / 野芝+洋芝"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        schema=SCHEMA,
    )

    table = sa.table(
        "racecourse_features",
        sa.column("course_code", sa.String),
        sa.column("course_name", sa.String),
        sa.column("direction", sa.SmallInteger),
        sa.column("straight_distance", sa.Numeric),
        sa.column("elevation_diff", sa.Numeric),
        sa.column("circuit_length", sa.Integer),
        sa.column("grass_type", sa.String),
        schema=SCHEMA,
    )

    op.bulk_insert(table, [
        {
            "course_code": code,
            "course_name": name,
            "direction": direction,
            "straight_distance": straight,
            "elevation_diff": elevation,
            "circuit_length": circuit,
            "grass_type": grass,
        }
        for code, name, direction, straight, elevation, circuit, grass in SEED_DATA
    ])


def downgrade() -> None:
    op.drop_table("racecourse_features", schema=SCHEMA)
