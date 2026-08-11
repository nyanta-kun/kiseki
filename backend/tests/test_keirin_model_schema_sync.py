"""SQLAlchemy モデルが alembic の実スキーマを取りこぼしていないことを検査する。

背景（2026-08-08 レビュー指摘 L-5）:
  `KeirinWtEntry` に `pred_win_pct` / `pred_top3_pct`（migration n0p1q2r3s4t5）、
  `KeirinPicksHistory` に `gate_label` / `win_rank` / `ratio`（migration l8m9n0p1q2r3）
  が **モデル側だけ欠落**していた。keirin_router は raw SQL でこれらを読んでおり
  本番は動いていたので、症状が出ないまま放置されていた。

  問題は `alembic revision --autogenerate` を実行したときで、モデルに無い列は
  「消された列」と解釈され **DROP COLUMN を含む migration が生成されうる**。
  CLAUDE.md の「Alembic 経由のみで DDL 変更」という運用と噛み合わない。

  migration ファイルを唯一の真実として、モデルが列を取りこぼしていないか照合する。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src.db.keirin_models import KeirinBase

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# keirin スキーマのテーブルに対する `op.add_column("<table>", sa.Column("<col>", ...)`
_ADD_COLUMN = re.compile(
    r'op\.add_column\(\s*["\'](?P<table>\w+)["\']\s*,\s*sa\.Column\(\s*["\'](?P<col>\w+)["\']')
_DROP_COLUMN = re.compile(
    r'op\.drop_column\(\s*["\'](?P<table>\w+)["\']\s*,\s*["\'](?P<col>\w+)["\']')


def _resolve(node: ast.AST, consts: dict[str, str]) -> str | None:
    """引数がテーブル名の文字列になるなら返す。モジュール定数も解決する。

    🔴 元は正規表現で `op.add_column("<table>", ...` の**文字列リテラルだけ**を
       見ていたため、`TABLE = "netkeirin_submissions"` のように**定数で書いた
       migration が丸ごと見えていなかった**（既存の盲点）。
       2026-08-11 に netkeirin_settings へ列を足して発覚。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def _keirin_migration_columns() -> dict[str, set[str]]:
    """keirin スキーマ向け migration が追加した {テーブル: 列集合}。

    `upgrade()` の add_column だけを見る（downgrade の drop_column は除く）。
    """
    out: dict[str, set[str]] = {}
    for path in sorted(_VERSIONS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if 'SCHEMA = "keirin"' not in text and 'schema="keirin"' not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            continue
        consts: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) \
                    and isinstance(node.targets[0], ast.Name):
                consts[node.targets[0].id] = node.value.value
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or fn.name != "upgrade":
                continue
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "attr", "")
                if name not in ("add_column", "drop_column") or not call.args:
                    continue
                table = _resolve(call.args[0], consts)
                if not table:
                    continue
                if name == "add_column":
                    col_call = call.args[1] if len(call.args) > 1 else None
                    col = (_resolve(col_call.args[0], consts)
                           if isinstance(col_call, ast.Call) and col_call.args else None)
                    if col:
                        out.setdefault(table, set()).add(col)
                else:
                    col = _resolve(call.args[1], consts) if len(call.args) > 1 else None
                    if col:
                        out.get(table, set()).discard(col)
    return out


def _model_columns() -> dict[str, set[str]]:
    return {
        t.name: {c.name for c in t.columns}
        for t in KeirinBase.metadata.tables.values()
    }


def test_migrations_are_discoverable() -> None:
    """照合対象が見つかること（0件だと以降のテストが全部素通りする）。"""
    cols = _keirin_migration_columns()
    assert cols, "keirin スキーマの migration から add_column を1件も抽出できなかった"
    assert "picks_history" in cols
    assert "wt_entries" in cols


def _checkable_tables() -> list[str]:
    """migration があり、かつ ORM モデルもあるテーブル。

    🔴 **対象を手書きで列挙しない。** 元は ["picks_history", "wt_entries"] の
       固定リストで、2026-08-11 に netkeirin_settings へ列を足したとき
       **素通りした**（このガードが守るはずの事故そのもの）。
       「一覧の手書き二重管理」はこのリポジトリで繰り返し事故を起こしている
       （keirin_netkeirin_7ss_submit_gap_2026_08_06 は同日3箇所）。
    """
    migrated, modelled = _keirin_migration_columns(), _model_columns()
    return sorted(set(migrated) & set(modelled))


def test_checkable_tables_cover_known_ones() -> None:
    """対象の自動抽出が既知のテーブルを取りこぼしていないこと。"""
    tables = _checkable_tables()
    for t in ("picks_history", "wt_entries", "netkeirin_settings"):
        assert t in tables, f"{t} が検査対象から漏れています"


def test_migrated_tables_without_model_are_known() -> None:
    """migration はあるが ORM モデルが無いテーブルを可視化する。

    モデルが無いと autogenerate は **テーブルごと DROP** を作りうる。
    既知の穴（raw SQL でしか触っていない表）だけを許容し、増えたら気づけるようにする。
    """
    known_gap = {"netkeirin_submissions"}
    orphans = set(_keirin_migration_columns()) - set(_model_columns())
    unexpected = orphans - known_gap
    assert not unexpected, (
        f"ORM モデルの無いテーブルに migration が増えています: {sorted(unexpected)}。"
        " autogenerate がテーブルごと DROP を作りうるのでモデルを用意すること")


@pytest.mark.parametrize("table", _checkable_tables())
def test_model_covers_every_migrated_column(table: str) -> None:
    """migration で追加した列が ORM モデルに載っていること。

    載っていないと `alembic revision --autogenerate` が DROP COLUMN を生成しうる。
    """
    migrated = _keirin_migration_columns().get(table, set())
    modelled = _model_columns().get(table, set())
    assert modelled, f"{table} のモデル定義が見つからない"
    missing = migrated - modelled
    assert not missing, (
        f"{table}: migration にあるのにモデルへ載っていない列 {sorted(missing)}。"
        " autogenerate が DROP COLUMN を作りうるので必ずモデルへ追加すること")
