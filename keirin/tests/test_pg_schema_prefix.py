"""keirin スキーマの自動付与（`_pg_translate`）の網羅テスト（2026-08-14）。

## 背景（実際に本番を止めた型）

`src/database.py::_pg_translate` の正規表現だけが、素の SELECT/UPDATE/DELETE に
`keirin.` を付ける唯一の経路。**INSERT 系はテーブル名を直接展開するので、
regex から漏れていても動いてしまう**。そのため漏れは「片方だけ動く」形で潜り、
実際に使う日まで気づけない。

- 2026-07-24: `netkeirin_submissions` を追加したとき regex への追加を忘れ、
  `_already_submitted()` の SELECT が**機能追加以来一度も動かず**、
  netkeirin 入稿が導入から一度も成功していなかった（INSERT だけ動いていた）。
- 2026-08-14: `netkeirin_sales_daily` / `netkeirin_sales_race` も同じく漏れていた
  （スクレイパはスキーマ修飾して書くので書き込みは動いており、
  素の SELECT を書いた瞬間に `relation does not exist` になる状態だった）。

## 何を守るか

keirin スキーマに存在するテーブルが、素の SQL でも `keirin.` を付けられること。
新しいテーブルを足したらこのリストにも足す（＝ regex への追加を強制する）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import _pg_translate  # noqa: E402

#: keirin スキーマのテーブル。**新設したらここへ足すこと。**
KEIRIN_TABLES = [
    "wt_races", "wt_entries", "wt_odds", "wt_odds_snapshot", "wt_weather",
    "venue_info", "picks_history", "model_evaluation",
    "netkeirin_settings", "netkeirin_submissions",
    "netkeirin_sales_daily", "netkeirin_sales_race",
]


@pytest.mark.parametrize("table", KEIRIN_TABLES)
def test_bare_select_gets_the_schema_prefix(table):
    sql, _ = _pg_translate(f"SELECT * FROM {table} WHERE x = ?", (1,))
    assert f"keirin.{table}" in sql, (
        f"{table} が keirin スキーマへ解決されない。"
        " src/database.py の _pg_translate の正規表現へ追加すること"
        "（漏れると素の SELECT だけが本番で落ちる）")


@pytest.mark.parametrize("table", KEIRIN_TABLES)
def test_already_qualified_is_not_double_prefixed(table):
    sql, _ = _pg_translate(f"SELECT * FROM keirin.{table}", ())
    assert "keirin.keirin." not in sql
    assert f"keirin.{table}" in sql


@pytest.mark.parametrize("table", KEIRIN_TABLES)
def test_update_and_delete_are_covered_too(table):
    for stmt in (f"UPDATE {table} SET a = 1", f"DELETE FROM {table}"):
        sql, _ = _pg_translate(stmt, ())
        assert f"keirin.{table}" in sql, f"{stmt} が解決されない"


def test_similar_names_are_not_mangled():
    """⚠️ 部分一致で他の識別子を壊さないこと（列名・別名・他スキーマ）。"""
    sql, _ = _pg_translate(
        "SELECT s.picks_history_id FROM other.wt_races_archive s", ())
    assert "keirin.wt_races_archive" not in sql
    assert "keirin.picks_history_id" not in sql
