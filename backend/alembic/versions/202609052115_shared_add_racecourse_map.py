"""add racecourse map

競馬場コードのサイト間対応表を `keiba` スキーマへ持つ。

背景（2026-09-05 の kiseki × sekito 統合調査）:
    この対応表は `sekito.racecourse`（25行）にしかなく、kiseki の地方系クエリが
    **SQL で直接 JOIN していた**:
      api/chihou_races_router.py / indices/chihou_calculator.py /
      services/chihou_recommender.py
    sekito スキーマを落とせなくする直接の障害なので、同じ内容を keiba 側へ移す。

    中央系は同じ対応を 4 箇所の Python dict に重複して持っていた
    （indices/anagusa.py / indices/paddock.py / api/races.py /
      services/recommender.py）。こちらは src/utils/racecourse.py に集約した。

🔴 seed をこのファイルに直書きしている理由:
    マイグレーションは「その時点のスナップショット」であるべきで、アプリ側の
    定数を import すると、後でモジュールが動いた・変わっただけで過去の
    マイグレーションが壊れる。重複は承知のうえで直書きし、代わりに
    backend/tests/test_racecourse_map_consistency.py が
    「最新の seed と src/utils/racecourse.py が一致すること」を機械的に固定する。

出典: `sekito.racecourse`（2026-09-05 時点の全25行）

Revision ID: 202609052115_shared
Revises: 202609021945_shared
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "202609052115_shared"
down_revision: str | Sequence[str] | None = "202609021945_shared"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "keiba"

# (code, name, display, netkeiba_id, book_id, kichiuma_id, heihachi_id, rakuten_id, jra_code)
SEED: tuple[tuple[str, str, int, str, str, str, str | None, str | None, str | None], ...] = (
    ("JSPK", "札幌", 1, "01", "08", "71", "札", None, "01"),
    ("JHKD", "函館", 2, "02", "09", "72", "函", None, "02"),
    ("JFKS", "福島", 3, "03", "06", "73", "福", None, "03"),
    ("JNGT", "新潟", 4, "04", "07", "74", "新", None, "04"),
    ("JTOK", "東京", 5, "05", "04", "75", "東", None, "05"),
    ("JNKY", "中山", 6, "06", "05", "76", "中", None, "06"),
    ("JCKO", "中京", 7, "07", "02", "77", "名", None, "07"),
    ("JKYO", "京都", 8, "08", "00", "78", "京", None, "08"),
    ("JHSN", "阪神", 9, "09", "01", "79", "阪", None, "09"),
    ("JKKR", "小倉", 10, "10", "03", "80", "小", None, "10"),
    ("NURW", "浦和", 11, "42", "13", "18", None, "1813", None),
    ("NFNB", "船橋", 12, "43", "12", "19", None, "1914", None),
    ("NOOI", "大井", 13, "44", "10", "20", None, "2015", None),
    ("NKWK", "川崎", 14, "45", "11", "21", None, "2135", None),
    ("NMNB", "門別", 15, "30", "42", "36", None, "3601", None),
    ("NMOR", "盛岡", 16, "35", "33", "10", None, "1006", None),
    ("NMSZ", "水沢", 17, "36", "29", "11", None, "1106", None),
    ("NKNZ", "金沢", 18, "46", "20", "22", None, "2218", None),
    ("NKSM", "笠松", 19, "47", "19", "23", None, "2320", None),
    ("NNGO", "名古屋", 20, "48", "34", "24", None, "2433", None),
    ("NSND", "園田", 21, "50", "37", "27", None, "2726", None),
    ("NHMD", "姫路", 22, "51", "39", "28", None, "2826", None),
    ("NKCH", "高知", 23, "54", "26", "31", None, "3129", None),
    ("NSGA", "佐賀", 24, "55", "23", "32", None, "3230", None),
    ("NOBH", "帯広(ば)", 99, "65", "58", "03", None, "0304", None),
)


def upgrade() -> None:
    table = op.create_table(
        "racecourse_map",
        sa.Column("code", sa.String(length=4), nullable=False,
                  comment="sekito 4文字コード。sekito.* の course_code と同じ"),
        sa.Column("name", sa.String(length=16), nullable=False, comment="表示名"),
        sa.Column("display", sa.Integer(), nullable=False, comment="表示順"),
        sa.Column("netkeiba_id", sa.String(length=4), nullable=False,
                  comment="netkeiba の場コード。中央は JRA 2桁と同じ、地方は別体系"),
        sa.Column("book_id", sa.String(length=4), nullable=True, comment="競馬ブックの場コード"),
        sa.Column("kichiuma_id", sa.String(length=4), nullable=True, comment="吉馬の場コード"),
        sa.Column("heihachi_id", sa.String(length=4), nullable=True,
                  comment="平八(note)の1文字表記。中央のみ"),
        sa.Column("rakuten_id", sa.String(length=8), nullable=True,
                  comment="楽天競馬の場コード。地方のみ"),
        sa.Column("jra_code", sa.String(length=2), nullable=True,
                  comment="JV-Link の2桁課コード。中央のみ（地方は JV-Link 管轄外で NULL）"),
        sa.PrimaryKeyConstraint("code"),
        schema=SCHEMA,
    )
    # netkeiba_id は中央・地方で体系が違い、両者をまたぐと衝突しうるので
    # 一意制約は付けない（例: 中央の "01"=札幌 と 地方の体系は別空間）。
    op.create_index(
        "idx_racecourse_map_netkeiba", "racecourse_map", ["netkeiba_id"], schema=SCHEMA
    )
    op.create_index(
        "idx_racecourse_map_jra", "racecourse_map", ["jra_code"], schema=SCHEMA
    )

    op.bulk_insert(
        table,
        [
            {
                "code": r[0], "name": r[1], "display": r[2], "netkeiba_id": r[3],
                "book_id": r[4], "kichiuma_id": r[5], "heihachi_id": r[6],
                "rakuten_id": r[7], "jra_code": r[8],
            }
            for r in SEED
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_racecourse_map_jra", table_name="racecourse_map", schema=SCHEMA)
    op.drop_index("idx_racecourse_map_netkeiba", table_name="racecourse_map", schema=SCHEMA)
    op.drop_table("racecourse_map", schema=SCHEMA)
