"""三連単オッズ予測（`src/odds_prediction_tf.py`）の回帰テスト（2026-08-12 新設）。

三連複版（`src/odds_prediction.py`）で踏んだ穴を三連単でも塞ぐ:

1. **train/serve skew** — 特徴量名の一覧が学習時と違ったら**起動時に落とす**。
   ずれても入稿は成功するので、実行時には気づけない
2. **板の整合** — `Σ(1/オッズ)` を目標総和へ合わせる再スケールを外すと、
   帯（例 130〜200倍）で切る用途がそのままずれる。**logMAE はこの欠陥に盲目**
3. **順列であること** — 三連複の `_pl_trio` は6順列を足し上げるが、三連単は
   足してはいけない。同じ3車でも 1-2-3 と 3-2-1 はオッズが10倍以上違う
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pytest

from src import odds_prediction_tf as m

CARS = list(range(1, 8))
P3 = dict(zip(CARS, [0.60, 0.55, 0.45, 0.40, 0.35, 0.35, 0.30]))
PW = dict(zip(CARS, [0.30, 0.22, 0.15, 0.12, 0.10, 0.06, 0.05]))


def _meta(cars=CARS):
    return {c: {"race_point": 100.0 + c, "mark": (c if c <= 3 else None),
                "player_class": "A1", "style": "逃",
                "line_group": "A" if c <= 3 else "B", "line_size": 3,
                "line_pos": c if c <= 3 else c - 3, "is_line_leader": int(c in (1, 4)),
                "first_rate": 0.1 * c, "second_rate": 0.05 * c, "third_rate": 0.02 * c}
            for c in cars}


# ── 順列であること ──────────────────────────────────────────────────

def test_pl_is_ordered_not_combinational():
    """順序が違えば確率も違うこと（三連複のように足し上げていない）。"""
    pl = m.pl_ordered(PW, CARS)
    assert len(pl) == 7 * 6 * 5, f"7車の順列は210通りのはず: {len(pl)}"
    assert pl[(1, 2, 3)] != pl[(3, 2, 1)]
    assert pl[(1, 2, 3)] > pl[(7, 2, 3)]   # 1着率の高い車を先頭に置くほうが確率が高い


def test_pl_sums_to_one():
    """全順列の確率が1になること（Plackett-Luce の定義）。"""
    assert m.pl_ordered(PW, CARS)
    assert sum(m.pl_ordered(PW, CARS).values()) == pytest.approx(1.0, abs=1e-9)


# ── 特徴量 ──────────────────────────────────────────────────────────

def test_features_cover_every_permutation():
    combos, X = m.build_race_features(CARS, P3, PW, _meta())
    assert len(combos) == 210
    assert X.shape == (210, len(m.FEATURE_NAMES))
    assert set(combos) == set(itertools.permutations(CARS, 3))
    assert np.isfinite(X).all(), "特徴量に NaN/inf がある"


def test_features_distinguish_order():
    """同じ3車でも着順が違えば特徴量が違うこと。

    ここが同じだと、モデルは 1-2-3 と 3-2-1 に同じオッズを出す。
    """
    combos, X = m.build_race_features(CARS, P3, PW, _meta())
    i, j = combos.index((1, 2, 3)), combos.index((3, 2, 1))
    assert not np.allclose(X[i], X[j])


def test_directional_line_features():
    """ライン特徴が向きを持つこと（集合ではなく着順で決まる）。"""
    combos, X = m.build_race_features(CARS, P3, PW, _meta())
    col = {n: k for k, n in enumerate(m.FEATURE_NAMES)}
    # 1,2 は同ライン(A)・line_pos は 1<2 なので順走
    i = combos.index((1, 2, 4))
    assert X[i][col["same_line_12"]] == 1
    assert X[i][col["line_order_12"]] == 1
    j = combos.index((2, 1, 4))       # 逆走
    assert X[j][col["same_line_12"]] == 1
    assert X[j][col["line_order_12"]] == 0


def test_missing_inputs_raise_rather_than_guess():
    """p3/pw やライン情報が欠けたら例外。黙って既定値で埋めない。"""
    with pytest.raises(m.OddsPredictionUnavailable):
        m.build_race_features(CARS, {**P3, 3: 0.0}, PW, _meta())
    bad = _meta()
    bad[5]["line_group"] = None
    with pytest.raises(m.OddsPredictionUnavailable):
        m.build_race_features(CARS, P3, PW, bad)
    with pytest.raises(m.OddsPredictionUnavailable):
        m.build_race_features(list(range(1, 10)), P3, PW, _meta())   # 9車は未対応


# ── train/serve skew ────────────────────────────────────────────────

def test_meta_mismatch_is_detected(tmp_path, monkeypatch):
    """特徴量名がずれたら `load_meta()` が落ちること。

    🔴 ここが素通りすると、列の順序違いのまま推論して**それらしい値**が出る。
    """
    p = tmp_path / "odds_tf_meta.json"
    p.write_text(json.dumps({"feature_names": list(m.FEATURE_NAMES)[:-1],
                             "target_sum": {"7": 1.336}}), encoding="utf-8")
    monkeypatch.setattr(m, "META_PATH", p)
    monkeypatch.setattr(m, "_META_CACHE", None)
    with pytest.raises(m.OddsPredictionUnavailable):
        m.load_meta()


def test_meta_matching_names_pass(tmp_path, monkeypatch):
    p = tmp_path / "odds_tf_meta.json"
    p.write_text(json.dumps({"feature_names": list(m.FEATURE_NAMES),
                             "target_sum": {"7": 1.336}}), encoding="utf-8")
    monkeypatch.setattr(m, "META_PATH", p)
    monkeypatch.setattr(m, "_META_CACHE", None)
    assert m.target_sum(7) == pytest.approx(1.336)


# ── 板の整合 ────────────────────────────────────────────────────────

def test_predict_board_rescales_to_target_sum(tmp_path, monkeypatch):
    """予測後に Σ(1/オッズ) が目標総和へ揃うこと。

    再スケールを外すと帯の狙いがそのままずれる（logMAE では見えない）。
    """
    target = 1.336
    p = tmp_path / "odds_tf_meta.json"
    p.write_text(json.dumps({"feature_names": list(m.FEATURE_NAMES),
                             "target_sum": {"7": target}}), encoding="utf-8")
    monkeypatch.setattr(m, "META_PATH", p)
    monkeypatch.setattr(m, "_META_CACHE", None)

    class _Stub:                      # 一律 log10(50) を返す＝整合していない板
        def predict(self, X):
            return np.full(len(X), np.log10(50.0))

    monkeypatch.setattr(m, "load_model", lambda n: _Stub())
    board = m.predict_board(CARS, P3, PW, _meta())
    assert len(board) == 210
    assert sum(1.0 / o for o in board.values()) == pytest.approx(target, rel=1e-6)
