"""`get_connection()` 経由の SQL で `%(name)s` 形式を使わせない。

## なぜ必要か

`_pg_translate` は「LIKE '7PLUS%' 等のリテラル `%`」を守るために
**SQL 中の `%` を無条件に `%%` へエスケープする**。その後で `:name` → `%(name)s`、
`?` → `%s` を当てる。したがって呼び出し側が最初から `%(name)s` と書くと

    "... WHERE race_date = %(d)s"  →  "... WHERE race_date = %%(d)s"

となり、psycopg2 が `%%` をリテラル `%` に戻すため **PostgreSQL には `%(d)s` が
そのまま届いて `syntax error at or near "%"` になる**。パラメータは一切渡らない。

2026-08-16（PR#194）に `cli/main.py` の cup_grade 取得がこの形で入り、
**導入初日 2026-08-17 から 10 日間、一度も値が取れていなかった**。
`except` で握って警告を出すだけの箇所だったため誰も気づかず、
`p3_calibration.grade_group(None)` が「F級」へ倒れて **GIII 開催の 7C/9C ゲートが
本来より緩んでいた**（8/17〜8/26 の GIII 48レースで通過 22件 → 26件）。

⚠️ この経路で使えるプレースホルダは **`?` と `:name` の2つだけ**。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import _pg_translate  # noqa: E402

#: `%(name)s` を書いてよいファイル。SQLAlchemy(`sa_text`) へ直接渡す経路は
#: `_pg_translate` を通らないので対象外。足すときは**理由を書くこと**。
_ALLOWED = {
    # feature_wt.py は SQLAlchemy engine を持つときだけ pg 用 SQL を組み、
    # sa_text() 経由で渡す（_pg_translate を通らない）。
    "src/preprocessing/feature_wt.py",
}

_PARAM_RE = re.compile(r"%\(\w+\)s")


def _sql_lines(path: Path):
    """SQL としてプレースホルダを含みうる行だけを返す。

    ⚠️ コメントと logging の書式（`%(asctime)s` 等）は別物なので外す。
       ここを緩めると本物の違反が騒音に埋もれる。
    """
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        st = line.strip()
        if st.startswith("#") or "logging" in line or "format=" in line:
            continue
        yield n, line


def test_translate_breaks_percent_named_style() -> None:
    """`%(name)s` は二重エスケープされて壊れる——この事実を固定する。"""
    sql, params = _pg_translate(
        "SELECT race_key, cup_grade FROM wt_races WHERE race_date = %(d)s",
        {"d": "2026-08-26"},
    )
    assert "%%(d)s" in sql, "エスケープの挙動が変わった。本テストの前提を見直すこと"


def test_translate_supports_qmark_and_colon() -> None:
    """使ってよい2つの形式は正しく変換される。"""
    sql, _ = _pg_translate(
        "SELECT race_key, cup_grade FROM wt_races WHERE race_date = ?", ("2026-08-26",))
    assert "= %s" in sql and "%%" not in sql
    sql2, _ = _pg_translate(
        "SELECT race_key FROM wt_races WHERE race_date = :d", {"d": "2026-08-26"})
    assert "= %(d)s" in sql2


def test_no_percent_named_placeholders_in_sources() -> None:
    """`get_connection()` を使うモジュールの SQL に `%(name)s` を書かない。

    ⚠️ 対象を「`get_connection` を import/使用しているファイル」に絞る。
       `scripts/backfill_*.py` などは psycopg2 へ直接つなぐので `%(name)s` が正しく、
       一律に禁止すると本物の違反が埋もれる。
    """
    offenders: list[str] = []
    for path in sorted((REPO / "src").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel in _ALLOWED or "/__pycache__/" in rel:
            continue
        text = path.read_text(encoding="utf-8")
        if "get_connection" not in text:
            continue
        for n, line in _sql_lines(path):
            if _PARAM_RE.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, (
        "`get_connection()` 経由では `%(name)s` は使えない（`%` が `%%` へ二重"
        "エスケープされ、パラメータが渡らないまま PostgreSQL へ届く）。"
        "`?` か `:name` に直すこと:\n  " + "\n  ".join(offenders))


def test_cup_grade_query_uses_qmark() -> None:
    """cup_grade の取得が `?` で書かれていること（2026-08-17 の回帰の再発防止）。"""
    src = (REPO / "src" / "cli" / "main.py").read_text(encoding="utf-8")
    assert "SELECT race_key, cup_grade FROM wt_races WHERE race_date = ?" in src, (
        "cup_grade の取得クエリが `?` 以外の形になっている。"
        "`%(d)s` に戻すと GIII の較正が黙って効かなくなる")
