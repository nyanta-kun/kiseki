"""`src/database.py` のスキーマ接頭辞リストの取りこぼしを検出する。

PostgreSQL 一本化（2026-07-22）以降、`get_connection()` は SQL 中のテーブル名へ
`keirin.` を**正規表現の手書きリスト**で付けている。ここへ足し忘れると
`relation "..." does not exist` で落ちる。

2026-08-18 に `wt_race_conditions` で実際に踏んだ。これは
`CURRENT_PAPER_RANKS` / `RANK_ORDER` / tail reconcile と**同じ「手書きリストへの
足し忘れ」パターン**で、このリポジトリで繰り返し起きている。ランクを1つずつ
検査するテストを増やしても次で漏れるので、**init_db が作るテーブル全件**を
対象に突き合わせる。

意図的に接頭辞を付けないものは `_INTENTIONALLY_UNPREFIXED` へ理由付きで書く。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

#: keirin スキーマへ載せていないテーブル（旧・自前スキーマ側の残置）。
#: これらは PG 上に無いか、あっても keirin 経路から触らない。
_INTENTIONALLY_UNPREFIXED = {
    "venues", "players", "races", "race_entries", "race_results", "odds",
}


def _source() -> str:
    return (REPO / "src" / "database.py").read_text(encoding="utf-8")


def _created_tables(src: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", src,
                          flags=re.IGNORECASE))


def _prefixed_tables(src: str) -> set[str]:
    """接頭辞付与の正規表現に列挙されているテーブル名を取り出す。

    ⚠️ **`re.sub` 呼び出しのその1箇所だけを見る。** ファイル全文から拾うと
       コメントや別の SQL に出てくる名前を「登録済み」と誤認する
       （tail reconcile のテストが同じ理由で一度無効化されていた）。
    """
    m = re.search(r'sql = re\.sub\(\s*r"\(\?<!\\w\)\(\?:keirin\\\.\)\?\('
                  r'(.*?)\)\\b"', src, flags=re.DOTALL)
    assert m, "接頭辞付与の re.sub が見つからない（database.py の形が変わった）"
    body = re.sub(r'"\s*\n\s*r"', "", m.group(1))       # 文字列連結を畳む
    return {t for t in body.split("|") if re.fullmatch(r"\w+", t)}


def test_every_created_table_is_schema_prefixed():
    src = _source()
    created = _created_tables(src)
    prefixed = _prefixed_tables(src)
    missing = created - prefixed - _INTENTIONALLY_UNPREFIXED
    assert not missing, (
        f"init_db が作るのに keirin. 接頭辞リストへ未登録: {sorted(missing)}。"
        " src/database.py の re.sub へ足すか、理由を"
        " _INTENTIONALLY_UNPREFIXED へ書くこと")


def test_longer_names_come_before_their_prefixes():
    """前方一致するテーブル名は長い方を先に置くこと。

    交替は左から試されるので、`wt_odds|wt_odds_snapshot` の順だと
    `wt_odds_snapshot` が `keirin.wt_odds_snapshot` にならず壊れる。
    """
    order = [t for t in _prefixed_tables(_source())]
    src = _source()
    m = re.search(r'\(\?:keirin\\\.\)\?\((.*?)\)\\b"', src, flags=re.DOTALL)
    body = re.sub(r'"\s*\n\s*r"', "", m.group(1))
    seq = [t for t in body.split("|") if re.fullmatch(r"\w+", t)]
    for i, a in enumerate(seq):
        for b in seq[i + 1:]:
            assert not b.startswith(a), (
                f"'{a}' が '{b}' より先にあるため '{b}' に接頭辞が付かない。"
                " 長い名前を先に置くこと")
    assert order  # 空リストで素通りしないこと


def test_wt_race_conditions_is_registered():
    """2026-08-18 に実際に踏んだ取りこぼしを名指しで固定する。"""
    assert "wt_race_conditions" in _prefixed_tables(_source())
