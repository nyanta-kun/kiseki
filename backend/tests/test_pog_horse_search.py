"""POG ドラフトの候補馬検索を固定する（統合 Phase 5 の 5e-1）。

なぜ必要か:
    ここは**利用者の入力をそのまま SQL の材料にする**唯一の POG API で、
    列名（`field`）と検索語（`q`）の両方が外から来る。どちらも素通しにすると
    壊れ方が静か（ワイルドカードなら「なぜか全件出る」だけ）なので、
    機械的に固定しておく。

    並び順の一意性も入れてある。移設元は `ORDER BY broodmare DESC` だけで
    ページ送りしており、母名が同じ馬（半兄弟）が複数いると**ページを跨いだ
    ときに同じ馬が二度出る／出ないまま飛ぶ**。これは目視では気づけない。
"""

from __future__ import annotations

import pytest

from src.services import pog_horse_search as m


def test_ワイルドカードをエスケープする():
    """`%` 一文字で全件になってしまわないこと。"""
    assert m._like("%") == r"%\%%"
    assert m._like("_") == r"%\_%"
    assert m._like("キズナ") == "%キズナ%"


def test_バックスラッシュを先にエスケープする():
    """順番を間違えると `\\%` が二重エスケープで壊れる。"""
    assert m._like("\\") == r"%\\%"
    assert m._like(r"a\%b") == r"%a\\\%b%"


def test_並び順が一意に決まる():
    """`LIMIT/OFFSET` を安定させるキーが ORDER BY に入っていること。"""
    sql = str(m._SEARCH_SQL)
    order = sql.split("ORDER BY", 1)[1]
    assert "netkeiba_horse_id" in order, "一意キーが無いとページ送りで重複・欠落が出る"


@pytest.mark.asyncio
async def test_知らない列は弾く():
    """白リストに無い `field` は DB に触る前に落ちること。"""
    with pytest.raises(ValueError, match="検索できない列"):
        await m.suggest(None, field="stable")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await m.suggest(None, field="1; DROP TABLE keiba.pog_horses --")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_白リストの列は列名として埋め込める():
    """`SEARCHABLE_FIELDS` が識別子として安全な形であること。

    ここを通った名前はそのまま SQL 文字列に入るので、英小文字と `_` だけに
    限る（将来 `broodmare_sire` 等を足すときの歯止め）。
    """
    for field in m.SEARCHABLE_FIELDS:
        assert field.replace("_", "").isalpha() and field.islower()


def test_移設元のテーブルを参照していない():
    """`keiba.provisional_horses` は Phase 5a で削除済み。

    移設元 sekito の同 API はこれを参照したまま残っており、本番で
    500 を返していた（nyanta-kun/sekito#42 で修復）。同じ穴を掘らないこと。
    """
    from pathlib import Path

    src = Path(m.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]  # docstring は経緯として言及してよい
    assert "provisional_horses" not in body


def test_馬名がNULLでも落とさない():
    """POG の指名対象は**未命名馬**。`name ILIKE` だけだと NULL が消える。

    2023年産は 7,765頭中 2,975頭が `name IS NULL`（父母は入っている）。
    `COALESCE` を外すとその馬が検索結果から静かに消え、エラーも出ない。
    """
    sql = str(m._SEARCH_SQL)
    where = sql.split("WHERE", 1)[1].split(")", 1)[0] + sql.split("WHERE", 1)[1]
    assert "COALESCE(name, '') ILIKE" in where, "NULL の馬名が絞り込みで消える"
