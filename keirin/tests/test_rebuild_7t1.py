"""7T1 の walk-forward 再構築の回帰テスト。

固定するのは「壊れても例外が出ない」不変条件:

1. **2026-01 より前を honest に作れない**（三連単オッズモデルの学習終端）
2. **1点買いを捨てない**（7T1 は1点が約半数。7H3 由来の `< 2` を残すと半分消える）
3. **賭け金は均等で引き直す**（7H3 の PL 比率引き継ぎを持ち込まない）
4. **tail reconcile へ登録されている**
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.backfill_7t1_rank_wt import (  # noqa: E402
    ODDS_TF_TRAIN_END, assert_odds_model_is_honest,
)

BACKFILL = REPO / "scripts" / "backfill_7t1_rank_wt.py"
REBUILD = REPO / "scripts" / "rebuild_7t1_walkforward_pg.py"


def test_train_end_matches_the_model_meta():
    """🔴 定数がモデルのメタとずれていないこと。

    ずれると「honest のつもりで look-ahead」または「使える期間を無駄に捨てる」。
    """
    import json
    meta = json.loads((REPO / "data" / "models" / "odds_tf_meta.json")
                      .read_text(encoding="utf-8"))
    assert ODDS_TF_TRAIN_END == meta["train_end"]


@pytest.mark.parametrize("date_from", ["2024-01-01", "2025-06-01", "2025-12-31"])
def test_rejects_dates_inside_the_training_window(date_from):
    """🔴 学習期間内を対象にしたら**落ちる**こと（黙って通さない）。"""
    with pytest.raises(SystemExit):
        assert_odds_model_is_honest(date_from)


@pytest.mark.parametrize("date_from", ["2026-01-01", "2026-08-01"])
def test_accepts_dates_after_the_training_window(date_from):
    assert_odds_model_is_honest(date_from) is None


def test_single_point_bets_are_kept():
    """🔴 1点買いを捨てないこと。

    7T1 は**1点が約半数**（自己整合で点数が決まるため）。7H3 から持ってきた
    `if len(legs) < 2: continue` を残すと母集団の半分が黙って消える。
    """
    src = BACKFILL.read_text(encoding="utf-8")
    assert "if len(legs) < 2:" not in src, (
        "7H3 由来の「2点未満は捨てる」が残っています。7T1 は1点買いが約半数です")
    assert "if not legs:" in src


def test_stakes_are_rebuilt_equally():
    """🔴 均等で引き直すこと（7H3 の PL 比率引き継ぎを持ち込まない）。"""
    src = BACKFILL.read_text(encoding="utf-8")
    assert "rank_7t1_stakes(legs)" in src
    assert "allocate_budget" not in src, (
        "PL 比率の引き継ぎが残っています。7T1 の配分は均等が仕様です")


def test_rebuild_drops_windows_before_the_model():
    """全期間再構築が学習期間内の窓を落とし、**落としたことを報告する**こと。"""
    src = REBUILD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "drop_windows_before_odds_model")
    body = ast.get_source_segment(src, fn)
    assert "ODDS_TF_TRAIN_END" in body
    assert "print(" in body, "落とした窓を黙って捨てています（期間の違いに気づけません）"


def test_registered_in_tail_reconcile():
    """🔴 未登録だと当月だけ live 行が残り rebuild 行と混在する（7H1 で実際に起きた）。"""
    sh = (REPO / "scripts" / "reconcile_walkforward_tail.sh").read_text(encoding="utf-8")
    line = next(l for l in sh.splitlines() if l.startswith("for spec in "))
    assert '"7t1:7T1"' in line, "tail reconcile の for 行に 7T1 がありません"
