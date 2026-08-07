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


def _keirin_migration_columns() -> dict[str, set[str]]:
    """keirin スキーマ向け migration が追加した {テーブル: 列集合}。

    `upgrade()` の add_column だけを見る（downgrade の drop_column は除く）。
    """
    out: dict[str, set[str]] = {}
    for p in sorted(_VERSIONS.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        if 'SCHEMA = "keirin"' not in text and 'schema="keirin"' not in text:
            continue
        upgrade = text.split("def upgrade", 1)[-1].split("def downgrade", 1)[0]
        for m in _ADD_COLUMN.finditer(upgrade):
            out.setdefault(m.group("table"), set()).add(m.group("col"))
        # 後続 migration で消された列は対象外にする
        for m in _DROP_COLUMN.finditer(upgrade):
            out.get(m.group("table"), set()).discard(m.group("col"))
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


@pytest.mark.parametrize("table", ["picks_history", "wt_entries"])
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
