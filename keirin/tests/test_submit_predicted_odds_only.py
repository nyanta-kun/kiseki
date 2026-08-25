"""入稿経路は**板を一切参照しない**（2026-08-26・ユーザー指示）。

## 経緯

2026-08-21 に「予測オッズを優先し、作れない目だけ板」へ反転したが、
**三連単には予測盤面を渡していなかった**ため実際には板のままだった。
8/22〜8/26 の実入稿で板由来だった 89点は**すべて 7H1 / 7T1**。
三連単の予測オッズ（`src.odds_prediction_tf`・7車）は既にあり、
7T1 / 7T3 の候補生成では使っていた——入稿経路だけが取り残されていた。

朝の板は薄い（2026-08-26 の熊本6Rは 35点中12点にしか金が入っていない）。
中途半端な板を混ぜると、同じ商品の中で金額の根拠が2種類になり、
想定オッズが説明できなくなる。

## ここで固定すること

1. `netkeirin_submit_wt` に**板を読む関数が存在しない**
2. `build_bet_lines` / `build_bet_detail` に**板を渡す引数が無い**（構造で塞ぐ）
3. 配分・ダッチ・足切り・前倒し判定が予測オッズ側を見ている

⚠️ `src/stake_allocation.landing_weights` は板を受け取れるままにしてある。
   過去の再構築（`src/rebuild_stakes.py`）は**当時の板**で組み直す必要があり、
   そちらへ予測オッズを渡すと look-ahead になる。入稿側が渡さないだけでよい。
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import netkeirin_submit_wt as sub  # noqa: E402

SRC = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
# コメント・docstring を除いた「実行されるコード」だけを見る
CODE = "\n".join(ln for ln in SRC.splitlines() if not ln.lstrip().startswith("#"))


def test_no_board_loader_exists():
    for name in ("_load_trio_board", "_load_trifecta_board", "_bet_detail_odds"):
        assert not hasattr(sub, name), f"板を読む関数 {name} が復活しています"


def test_no_sql_against_the_odds_tables():
    """SQL そのものを禁止する。関数名を変えて復活させても引っかかる。"""
    for table in ("wt_odds_snapshot", "wt_odds"):
        assert not re.search(rf'FROM\s+{table}\b', CODE, re.IGNORECASE), (
            f"入稿経路が {table} を読んでいます")


def test_bet_detail_has_no_board_parameter():
    """構造で塞ぐ。混ぜたくても混ぜられないようにする。"""
    for fn in (sub.build_bet_lines, sub.build_bet_detail):
        params = list(inspect.signature(fn).parameters)
        assert "odds" not in params, f"{fn.__name__} に板を渡せる引数が残っています"
        assert "predicted_odds" in params


def test_tilted_stakes_is_given_no_board():
    """配分は予測オッズ単独。板を渡すと `landing_weights` が blend へ落ちる。"""
    i = CODE.index("def _build_tilted_legs(")
    body = CODE[i:CODE.index("\ndef ", i + 10)]
    assert "tilted_stakes(" in body
    assert re.search(r"tilted_stakes\(\s*\n?\s*partners,\s*None,", body), (
        "朝の板を `tilted_stakes` へ渡しています")


def test_dutch_uses_the_predicted_trifecta_board():
    i = CODE.index("def _normalize_formation_candidate(")
    body = CODE[i:CODE.index("\ndef ", i + 10)]
    assert "_predicted_tf_fill(" in body, "ダッチ配分が三連単の予測盤面を使っていない"


def test_pull_forward_uses_the_predicted_trifecta_board():
    i = CODE.index("def _can_pull_forward(")
    body = CODE[i:CODE.index("\ndef ", i + 10)]
    assert "_predicted_tf_fill(" in body, "前倒し判定が三連単の予測盤面を見ていない"


def test_trifecta_board_is_cached_per_race():
    """210点の推論を1レースで何度も回さない（表示・ダッチ・前倒しで呼ばれる）。"""
    assert hasattr(sub, "_TF_BOARD_CACHE")
    i = CODE.index("def _predicted_tf_fill(")
    body = CODE[i:CODE.index("\ndef ", i + 10)]
    assert "_TF_BOARD_CACHE" in body
