"""型ラベルは**焼き付けた値から**再構成する（2026-09-21 新設）。

## 何を守るか

過去のレースの型を `wt_entries` から組み直すと**再現しない**。実測
（`type_lab_picks` の `mode IN ('live','live9')` 1,872R）:

    axis_sum が焼き付けと違うレース  1,870 / 1,872 = **99.89%**
    型ラベルが変わるレース           113 / 1,872 = **6.04%**
      firm（axis_sum）だけ反転  73 (3.90%)   ← p3 の二重ソース
      arare（behind）だけ変化   32 (1.71%)   ← 開催中に更新される列
      両方                       8 (0.43%)

原因はどちらも「後から直せない」性質のもの:

- `wt_entries.pred_top3_pct` は入稿後に `backfill_index_pct_wt.py` が
  **月次 vintage モデルで上書き**する。商品が使ったのは生成時の生の出力
- `ex_left_behind_pct` は**開催中に更新される**（同一節の連続日で 15.00% が変化）

🟢 **型の再現に要る値は全部 `type_lab_picks` に焼き付いている**
   （`axis_sum` / `arare` / `type_label`）。だから分析は組み直さずに
   `type_label_of(axis_sum, arare)` を通せばよい。ここではその写像が
   `race_shape` 本体と**同じ規則**であることを固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.type_lab import AXIS_SUM_FIRM, race_shape, type_label_of  # noqa: E402


@pytest.mark.parametrize("axis_sum,arare,want", [
    (1.50, -3, "A"), (1.50, -1, "A"), (1.50, 0, "B"), (1.50, 1, "C"), (1.50, 9, "C"),
    (1.40, -3, "D"), (1.40, -1, "D"), (1.40, 0, "E"), (1.40, 1, "F"), (1.40, 9, "F"),
])
def test_mapping(axis_sum, arare, want):
    assert type_label_of(axis_sum, arare) == want


def test_boundary_is_inclusive():
    """ちょうど閾値は「堅い」側（`>=`）。"""
    assert type_label_of(AXIS_SUM_FIRM, 0) == "B"
    assert type_label_of(AXIS_SUM_FIRM - 1e-9, 0) == "E"


def _shape(p3, **kw):
    cars = list(p3)
    base = dict(
        line_group={c: "A" for c in cars}, line_pos={c: 1 for c in cars},
        style={c: "逃" for c in cars}, race_point={c: 90.0 for c in cars},
        behind_pct={c: 0.0 for c in cars}, day_index=2, win_probs=None)
    base.update(kw)
    return race_shape(p3, base["line_group"], base["line_pos"], base["style"],
                      base["race_point"], base["behind_pct"], base["day_index"],
                      base["win_probs"])


def test_race_shape_uses_the_same_rule():
    """🔴 `race_shape` が返すラベルと `type_label_of` が一致すること。

    片方だけ変えると「画面の型」と「分析の型」が食い違う。
    """
    for top in (0.95, 0.80, 0.72, 0.60, 0.40):
        p3 = {1: top, 2: top - 0.02, 3: 0.45, 4: 0.40, 5: 0.35, 6: 0.30, 7: 0.20}
        sh = _shape(p3)
        assert sh is not None
        assert sh.type_label == type_label_of(sh.axis_sum, sh.arare)
        assert sh.firm == (sh.axis_sum >= AXIS_SUM_FIRM)
