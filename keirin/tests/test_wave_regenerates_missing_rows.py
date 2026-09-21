"""昼・夕の波は「行が1つも無いレース」も組み直す（2026-09-21 新設）。

## 何を守るか（本番で商品が丸ごと消えた）

2026-09-20（#593）から `build_type_lab_picks` は並び予想・AI印が未公開の
レースを生成時点で弾く（`_lineup_issue`）。それ自体は正しいが、
**`type_lab_picks` に行が1つも残らない**。

昼・夕の波の候補は `_load_rows`＝**`type_lab_picks` から**読むので、
行が無いレースは `todo` にすら入らず**永久に拾い直されない**。

実害 2026-09-21: ミッドナイトの高知(74)・佐世保(85) 18R が picks 0件・入稿0件。
9/10〜9/20 は毎日 16〜18R で picks があり 10〜18件入稿していたので、
**この日だけ丸ごと消えた**。夕方の波のログも
「7車 3R を組み直します／入稿 0件」で、候補に入っていなかった。

⚠️ ミッドナイトは朝7:15 時点で並び・印が未公開なのが**通常**なので、
   この経路が無いとミッドナイトは構造的に毎日消える。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SUBMIT = REPO / "scripts" / "netkeirin_submit_type_lab.py"


def _src() -> str:
    return SUBMIT.read_text(encoding="utf-8")


def test_helper_exists_and_reads_wt_races():
    """候補の欠落は `type_lab_picks` ではなく `wt_races` からしか見つけられない。"""
    src = _src()
    assert "def _races_missing_rows(" in src
    fn = src.split("def _races_missing_rows(", 1)[1].split("\ndef ", 1)[0]
    assert "FROM wt_races" in fn, "wt_races を見ていない（行が無いレースは見つからない）"
    assert "NOT EXISTS" in fn and "type_lab_picks" in fn
    assert '"live", "live9"' in fn, "mode を絞っていない"


def test_closed_races_are_excluded():
    """締切を過ぎたレースを組み直さない（出しても弾かれるだけ）。"""
    fn = _src().split("def _races_missing_rows(", 1)[1].split("\ndef ", 1)[0]
    assert "if str(rk) in closed:" in fn


def test_rebuild_todo_includes_missing_rows():
    """`todo` へ合流していること（呼んでいるだけで使っていない、を防ぐ）。"""
    src = _src()
    run = src.split("def run(day: str", 1)[1]
    assert "_races_missing_rows(day, closed)" in run
    i_call = run.index("_races_missing_rows(day, closed)")
    i_rebuild = run.index("rebuild(day, sorted(set(keys)), n_cars)")
    assert i_call < i_rebuild, "組み直しより後に拾っている"
    assert "todo.setdefault(n_cars, []).extend(keys)" in run


def test_only_runs_outside_morning_and_not_in_dry_run():
    """🔴 dry-run では書かない・朝は全レースを組むので不要、という既存条件を保つ。"""
    run = _src().split("def run(day: str", 1)[1]
    block = run.split("if do_rebuild and (session != \"morning\" or only_key) and not dry_run:", 1)
    assert len(block) == 2, "組み直しの条件節が変わっている"
    body = block[1].split("\n    elif ", 1)[0]
    assert "_races_missing_rows(day, closed)" in body, "条件節の外で呼んでいる"


def test_parses():
    ast.parse(_src())
