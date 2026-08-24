"""記録側の賭け金を**実際に入稿した配分**に合わせる規則を固定する（2026-08-24）。

## なぜ

記録側（`rebuild_stakes`）は 2026-08-07 の規則「朝オッズ×p3」で組み直していたが、
**入稿側は 2026-08-11 に「予測オッズの 1/オッズ 単独」へ移った**。以来ずっと別の
配分で記録しており、実測（2026-08-16〜・実入稿と突合 107件）で

    記録側 ROI 63.3% ↔ 実入稿 ROI 78.0%   （**−14.7pt**・的中39件中35件で不一致）

＝ **Web に出ている実績が、実際に売った商品を説明していなかった。**

🔴 予測オッズで組み直す案は採らない（オッズ予測モデルは `train_end: 2026-08-04` で
   それ以前へ当てると model-vintage look-ahead）。**記録された事実をそのまま使う。**
"""
from __future__ import annotations

import re
from pathlib import Path

from src.rebuild_stakes import stakes_for_combos

ROOT = Path(__file__).resolve().parent.parent

A1, A2 = 1, 2
COMBOS = [frozenset({A1, A2, t}) for t in (3, 4, 5)]
P3 = {3: 0.5, 4: 0.4, 5: 0.3}


def test_実配分があればそのまま使う():
    sub = {frozenset({1, 2, 3}): 5300, frozenset({1, 2, 4}): 3100,
           frozenset({1, 2, 5}): 1600}
    got = stakes_for_combos(A1, A2, COMBOS, P3, submitted=sub)
    assert got == sub, "入稿した配分をそのまま返していない"


def test_目が食い違うときは使わない():
    """🔴 欠車・再入稿で買い目が違えば**別の商品**。混ぜると点数も合計額も壊れる。"""
    sub = {frozenset({1, 2, 3}): 5000, frozenset({1, 2, 6}): 5000}   # 5 ではなく 6
    got = stakes_for_combos(A1, A2, COMBOS, P3, submitted=sub)
    assert set(got) == set(COMBOS), "買い目が入れ替わっている"
    assert got != sub
    assert sum(got.values()) <= 10000


def test_実配分が無ければ従来のモデル規則():
    a = stakes_for_combos(A1, A2, COMBOS, P3)
    b = stakes_for_combos(A1, A2, COMBOS, P3, submitted=None)
    assert a == b and sum(a.values()) <= 10000


def test_予測オッズは今も渡さない():
    """🔴 `train_end: 2026-08-04` のモデルを過去へ当てると look-ahead になる。

    実配分を使う方式はモデルを介さないので、この禁止は**引き続き有効**。
    """
    src = (ROOT / "src" / "rebuild_stakes.py").read_text("utf-8")
    assert "odds_prediction" not in src.replace("`src.odds_prediction`", ""), \
        "再構築が予測オッズを使い始めている"
    assert "予測オッズを渡してはいけない" in src, "禁止の根拠コメントが消えている"


def test_三連単の行は取り込まない():
    """⚠️ 三連単は順序つきなので frozenset へ畳むと別の目と衝突する。"""
    src = (ROOT / "src" / "rebuild_stakes.py").read_text("utf-8")
    fn = src[src.index("def load_submitted_stakes("):src.index("def stakes_for_combos(")]
    assert 'ln.get("bet_type") != "3連複"' in fn, "券種の絞り込みが無い"


def test_全ランクの再構築が実配分を読んでいる():
    """🔴 1つでも配線が漏れると、そのランクだけ古い配分で記録され続ける。"""
    for f, label in (("7s", "7S"), ("7c", "7C"), ("7b", "7B"), ("7m1", "7M1"),
                     ("9c", "9C"), ("7a", "7A"), ("7ss", "7SS")):
        src = (ROOT / "scripts" / f"backfill_{f}_rank_wt.py").read_text("utf-8")
        assert f'load_submitted_stakes(' in src, f"{f}: 実配分を読んでいない"
        assert re.search(rf'load_submitted_stakes\(.*?"{label}"\)', src, re.S), \
            f"{f}: ランク記号が {label} でない"
        assert "submitted=submitted_stakes.get(rk)" in src, f"{f}: 渡していない"
