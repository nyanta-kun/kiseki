"""POG ドラフトの候補馬検索（sekito `/api/pog/aobon` `/api/pog/suggest` の移設先）。

統合 Phase 5 の 5e-1。ドラフトで「指名する馬を探す」ための読み取りだけをここに置く。
指名の書き込み（`/user` `/roll` `/confirm`）は認証が要るので別で扱う。

## 🔴 移設元は本番で 500 を返していた

`routes/pog.js` は `sekito.horse` と `keiba.provisional_horses` を UNION して
いたが、後者は 2026-09-08 に統合 Phase 5a で削除した（後継が `keiba.pog_horses`）。
削除時に参照元を kiseki 内でしか探しておらず、sekito 側が残っていた:

    relation "keiba.provisional_horses" does not exist   ← /suggest /aobon

ドラフトは年 1 回・春だけの画面なので 2026-09-08 から誰も踏んでいない。
sekito 側は別途直した（nyanta-kun/sekito#42）。こちらは最初から
`keiba.pog_horses` だけを見る。

## 母集団

`keiba.pog_horses`（`scrapers/netkeiba/pog_horse_list.py` が貯めている）。

    2023年産  7,766頭
    2024年産  7,945頭
    2003〜2022年  各 49〜70頭  ← 過去の指名馬を移設した分だけ。検索には使えない

つまり**実用になるのは直近 2 年ぶん**で、それはドラフトの用途と一致する。
古い年度を指定しても落ちはしないが、ほぼ指名済みの馬しか出ない。

## 🔴 馬名は NULL でありうる。`name ILIKE` で素朴に絞ると消える

POG は**まだ名前の付いていない当歳・1歳馬**を指名する遊びなので、名前が
無い馬こそ候補の本体になる。netkeiba は未命名馬に「（母名）の2024」という
仮の名を振るが、それすら取れていない行が実在する:

    2024年産  7,945頭中  name IS NULL は 0頭
    2023年産  7,765頭中  name IS NULL は 2,975頭   ← 父母も性別も入っているのに名前だけ空

`name ILIKE '%%'` は NULL に**一致しない**ので、素朴に書くと 2,975頭が
検索結果から静かに消える（エラーも警告も出ない）。`COALESCE(name, '')` を
通すこと。2023年産の欠けは 5a のスクレイパ側の取りこぼしで、次の
2025年産（2027年春のドラフト対象）で再発すると候補が丸ごと欠ける。

## ⚠️ 並び順に一意な決着を付ける

移設元は `ORDER BY broodmare DESC` だけで `LIMIT/OFFSET` していた。母の名が
同じ馬（＝半兄弟）が複数いると順序が不定になり、**ページを跨ぐと同じ馬が
二度出たり、出ないまま飛ばされたりする**。`netkeiba_horse_id` を最後の
キーに足して一意にしてある。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 検索・サジェストで使ってよい列。**ここに無い名前を SQL へ入れない**
#: （呼び出し側から列名が来るため。素通しにすると SQL インジェクションになる）。
SEARCHABLE_FIELDS = ("name", "sire", "broodmare")

# ⚠️ 産年の絞り込みを `:birth_year::int` と書いてはいけない。SQLAlchemy が `::` を
#    型キャストと解釈してプレースホルダを展開せず、Postgres 側で
#    `syntax error at or near ":"` になる。`CAST(... AS int)` を使うこと。
#    ⚠️ 単体テストでは捕まらない（SQL 文字列を組み立てるだけでは落ちない）。
_SEARCH_SQL = text(
    """
    WITH pool AS (
        SELECT
            netkeiba_horse_id,
            name,
            COALESCE(sire, '')           AS sire,
            COALESCE(broodmare, '')      AS broodmare,
            COALESCE(broodmare_sire, '') AS broodmare_sire,
            sex,
            COALESCE(stable, '')         AS stable,
            birth_year
        FROM keiba.pog_horses
        WHERE (CAST(:birth_year AS int) IS NULL OR birth_year = CAST(:birth_year AS int))
          AND COALESCE(name, '') ILIKE :name
          AND COALESCE(sire, '')      ILIKE :sire
          AND COALESCE(broodmare, '') ILIKE :broodmare
          AND netkeiba_horse_id IS NOT NULL
    )
    SELECT *, COUNT(*) OVER() AS total_count
    FROM pool
    ORDER BY broodmare DESC, COALESCE(name, ''), netkeiba_horse_id
    LIMIT :limit OFFSET :offset
    """
)


def _like(value: str) -> str:
    """部分一致のパターンにする。

    ⚠️ `%` と `_` は ILIKE のワイルドカードなので、利用者が入れた分は
    そのまま渡さずエスケープする（`%` 一文字で全件になってしまう）。
    """
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_horses(
    db: AsyncSession,
    *,
    birth_year: int | None = None,
    name: str = "",
    sire: str = "",
    broodmare: str = "",
    page: int = 1,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """候補馬を検索し `(items, total)` を返す。

    Args:
        db: DB セッション。
        birth_year: 産年。None なら全年度（実用上は指定する）。
        name: 馬名の部分一致。
        sire: 父名の部分一致。
        broodmare: 母名の部分一致。
        page: 1 起点のページ番号。
        limit: 1 ページの件数。

    Returns:
        `(その頁の馬のリスト, 条件に合う総数)`。
    """
    page = max(1, page)
    rows = await db.execute(
        _SEARCH_SQL,
        {
            "birth_year": birth_year,
            "name": _like(name),
            "sire": _like(sire),
            "broodmare": _like(broodmare),
            "limit": limit,
            "offset": (page - 1) * limit,
        },
    )
    items = [dict(r) for r in rows.mappings()]
    total = int(items[0].pop("total_count")) if items else 0
    for item in items[1:]:
        item.pop("total_count", None)
    return items, total


async def suggest(
    db: AsyncSession,
    *,
    field: str,
    q: str = "",
    birth_year: int | None = None,
    limit: int = 20,
) -> list[str]:
    """検索欄の入力補完。`field` の値を重複なく返す。

    Args:
        db: DB セッション。
        field: `SEARCHABLE_FIELDS` のいずれか。
        q: 部分一致させる文字列。
        birth_year: 産年で絞る（None なら全年度）。
        limit: 返す件数の上限。

    Raises:
        ValueError: `field` が `SEARCHABLE_FIELDS` に無いとき。
    """
    if field not in SEARCHABLE_FIELDS:
        raise ValueError(f"検索できない列です: {field!r}")
    # field は上の白リストを通ったものだけなので、ここでの埋め込みは安全。
    sql = text(
        f"""
        SELECT DISTINCT {field} AS val
        FROM keiba.pog_horses
        WHERE (CAST(:birth_year AS int) IS NULL OR birth_year = CAST(:birth_year AS int))
          AND {field} ILIKE :q
          AND {field} IS NOT NULL
          AND {field} <> ''
        ORDER BY val
        LIMIT :limit
        """
    )
    rows = await db.execute(
        sql, {"birth_year": birth_year, "q": _like(q), "limit": limit}
    )
    return [r[0] for r in rows]
