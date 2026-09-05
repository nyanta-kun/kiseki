"""競馬場コードの対応表（サイト間マッピングの単一の出所）。

背景（2026-09-05 の kiseki × sekito 統合調査で整理）:
    同じ競馬場を、関わるシステムがそれぞれ別のコードで呼んでいる。

      - JRA-VAN / JV-Link ... 2桁の課コード ("01" = 札幌)
      - sekito ............... 4文字コード ("JSPK" = 札幌 / "NOOI" = 大井)
      - netkeiba ............. 2桁 ("01" / 中央と地方で体系が違う。大井は "44")
      - 吉馬 / 競馬ブック / 平八 / 楽天 ... それぞれ独自 ID

    この対応表は `sekito.racecourse`（25行）にあり、**kiseki は同じ対応を
    4 箇所の Python dict に重複して持っていた**:
      indices/anagusa.py / indices/paddock.py / api/races.py / services/recommender.py
    いずれも中央10場ぶんだけの部分コピーで、地方は持っていない。

    一方、地方側は SQL で `sekito.racecourse` に直接 JOIN している:
      api/chihou_races_router.py / indices/chihou_calculator.py /
      services/chihou_recommender.py
    **これは sekito スキーマを落とせなくする直接の障害**なので、同じ内容を
    `keiba.racecourse_map` に持ち、SQL はそちらを見るようにした
    （alembic: 202609052115_shared_add_racecourse_map）。

このモジュールの位置づけ:
    Python から使う対応表の**唯一の出所**。SQL 側は `keiba.racecourse_map` を
    見る。両者がずれると sekito 由来のデータが黙って JOIN から外れるため、
    `backend/tests/test_racecourse_map_consistency.py` が
    「マイグレーションの seed とこのモジュールが一致すること」を機械的に固定している。

    🔴 競馬場を足す・変えるときは **このモジュールと新しいマイグレーションの両方**を
    直すこと。片方だけ直すとテストが落ちる（落ちるように作ってある）。

出典: `sekito.racecourse`（2026-09-05 時点の全25行）。
"""

from __future__ import annotations

from typing import NamedTuple


class Racecourse(NamedTuple):
    """1 競馬場ぶんのコード対応。

    Attributes:
        code: sekito の 4 文字コード。`sekito.*` テーブルの course_code。
        name: 表示名。
        display: sekito 内の表示順。
        netkeiba_id: netkeiba の場コード（中央は JRA 2桁と同じ、地方は別体系）。
        book_id: 競馬ブックの場コード。
        kichiuma_id: 吉馬の場コード。
        heihachi_id: 平八（note 記事）の1文字表記。中央のみ。
        rakuten_id: 楽天競馬の場コード。地方のみ。
        jra_code: JRA-VAN / JV-Link の 2 桁課コード。中央のみ。
    """

    code: str
    name: str
    display: int
    netkeiba_id: str
    book_id: str
    kichiuma_id: str
    heihachi_id: str | None
    rakuten_id: str | None
    jra_code: str | None


# 中央は netkeiba_id が JRA 2 桁課コードと一致するため jra_code に同じ値を入れる。
# 地方は JV-Link の管轄外なので jra_code は None。
RACECOURSES: tuple[Racecourse, ...] = (
    # --- 中央（JRA）10場 ---
    Racecourse("JSPK", "札幌", 1, "01", "08", "71", "札", None, "01"),
    Racecourse("JHKD", "函館", 2, "02", "09", "72", "函", None, "02"),
    Racecourse("JFKS", "福島", 3, "03", "06", "73", "福", None, "03"),
    Racecourse("JNGT", "新潟", 4, "04", "07", "74", "新", None, "04"),
    Racecourse("JTOK", "東京", 5, "05", "04", "75", "東", None, "05"),
    Racecourse("JNKY", "中山", 6, "06", "05", "76", "中", None, "06"),
    Racecourse("JCKO", "中京", 7, "07", "02", "77", "名", None, "07"),
    Racecourse("JKYO", "京都", 8, "08", "00", "78", "京", None, "08"),
    Racecourse("JHSN", "阪神", 9, "09", "01", "79", "阪", None, "09"),
    Racecourse("JKKR", "小倉", 10, "10", "03", "80", "小", None, "10"),
    # --- 地方（NAR）15場 ---
    Racecourse("NURW", "浦和", 11, "42", "13", "18", None, "1813", None),
    Racecourse("NFNB", "船橋", 12, "43", "12", "19", None, "1914", None),
    Racecourse("NOOI", "大井", 13, "44", "10", "20", None, "2015", None),
    Racecourse("NKWK", "川崎", 14, "45", "11", "21", None, "2135", None),
    Racecourse("NMNB", "門別", 15, "30", "42", "36", None, "3601", None),
    Racecourse("NMOR", "盛岡", 16, "35", "33", "10", None, "1006", None),
    Racecourse("NMSZ", "水沢", 17, "36", "29", "11", None, "1106", None),
    Racecourse("NKNZ", "金沢", 18, "46", "20", "22", None, "2218", None),
    Racecourse("NKSM", "笠松", 19, "47", "19", "23", None, "2320", None),
    Racecourse("NNGO", "名古屋", 20, "48", "34", "24", None, "2433", None),
    Racecourse("NSND", "園田", 21, "50", "37", "27", None, "2726", None),
    Racecourse("NHMD", "姫路", 22, "51", "39", "28", None, "2826", None),
    Racecourse("NKCH", "高知", 23, "54", "26", "31", None, "3129", None),
    Racecourse("NSGA", "佐賀", 24, "55", "23", "32", None, "3230", None),
    Racecourse("NOBH", "帯広(ば)", 99, "65", "58", "03", None, "0304", None),
)

# JRA 2 桁課コード → sekito 4 文字コード（中央のみ）
JRA_TO_SEKITO: dict[str, str] = {
    rc.jra_code: rc.code for rc in RACECOURSES if rc.jra_code is not None
}

# sekito 4 文字コード → JRA 2 桁課コード（中央のみ）
SEKITO_TO_JRA: dict[str, str] = {v: k for k, v in JRA_TO_SEKITO.items()}

# sekito 4 文字コード → Racecourse（中央・地方すべて）
BY_CODE: dict[str, Racecourse] = {rc.code: rc for rc in RACECOURSES}


def is_jra(code: str) -> bool:
    """sekito の 4 文字コードが中央のものか。

    先頭 1 文字が 'J' かどうかで判定する。この規則は `sekito.races` を
    中央・地方に分ける全クエリで使われている前提。
    """
    return code.startswith("J")
