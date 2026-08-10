"""オッズ予測モデルの配線を固定する検査（2026-08-11）。

守りたいのは「壊れても例外が出ない」性質:
  1. 予測オッズがあるとき **p3 と blend せず単独で使う**（blend すると p3 の二重計上）
  2. 予測オッズは **朝の板より優先**される
  3. 買う点が1つでも欠けたら予測オッズを**使わない**（比率が壊れるため）
  4. 出どころ `source` が `predicted` になる（無言で従来経路へ落ちたら検知できる）
  5. 特徴量名が学習時と食い違ったら**推論を拒否**する（順序違いは特に検知しにくい）
  6. 予測盤面は **Σ(1/オッズ) が目標総和に一致**する（板として整合している）
  7. 再構築経路 `rebuild_stakes` は予測オッズを**使わない**（model-vintage look-ahead）
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pytest

from src import odds_prediction as op
from src.stake_allocation import (
    SOURCE_BLEND,
    SOURCE_MODEL,
    SOURCE_PREDICTED,
    landing_weights,
    tilted_stakes,
)


# ---------------------------------------------------------------------------
# 1〜4. 配分側の規則
# ---------------------------------------------------------------------------
LEGS = [3, 4, 5, 6, 7]
PRED = {3: 10.0, 4: 20.0, 5: 40.0, 6: 80.0, 7: 160.0}
MORNING = {3: 5.0, 4: 5.0, 5: 5.0, 6: 5.0, 7: 5.0}
P3 = {3: 0.5, 4: 0.4, 5: 0.3, 6: 0.2, 7: 0.1}


def test_predicted_odds_used_alone_not_blended():
    """予測オッズは 1/o そのもの。p3 との相乗平均になっていないこと。"""
    w, source = landing_weights(LEGS, None, P3, predicted_odds=PRED)
    assert source == SOURCE_PREDICTED
    expected = {t: 1.0 / PRED[t] for t in LEGS}
    total_w, total_e = sum(w.values()), sum(expected.values())
    for t in LEGS:
        assert w[t] / total_w == pytest.approx(expected[t] / total_e, rel=1e-9), (
            "予測オッズが p3 と blend されている。二重計上になり実質的中率が落ちる"
        )


def test_predicted_odds_beats_morning_board():
    w, source = landing_weights(LEGS, MORNING, P3, predicted_odds=PRED)
    assert source == SOURCE_PREDICTED
    # 朝の板は全点同値なので、それが採用されていれば重みは一様になる
    assert len(set(round(v, 12) for v in w.values())) > 1, "朝の板が優先されている"


def test_partial_predicted_odds_is_rejected():
    """1点でも欠けたら使わない。混ぜると比率が壊れる。"""
    partial = {t: PRED[t] for t in LEGS[:-1]}
    _, source = landing_weights(LEGS, None, P3, predicted_odds=partial)
    assert source == SOURCE_MODEL
    _, source = landing_weights(LEGS, MORNING, P3, predicted_odds=partial)
    assert source == SOURCE_BLEND


@pytest.mark.parametrize("bad", [None, {}, {3: 0, 4: 1, 5: 1, 6: 1, 7: 1},
                                 {3: -1, 4: 1, 5: 1, 6: 1, 7: 1}])
def test_invalid_predicted_odds_falls_back(bad):
    _, source = landing_weights(LEGS, None, P3, predicted_odds=bad)
    assert source == SOURCE_MODEL


def test_tilted_stakes_passes_predicted_through():
    stakes, source = tilted_stakes(LEGS, MORNING, P3, budget=10_000,
                                   predicted_odds=PRED)
    assert source == SOURCE_PREDICTED
    assert sum(stakes.values()) == 10_000
    assert all(v >= 100 for v in stakes.values()), "全点に最低1単位が要る"
    # オッズが低い点ほど厚く張る（払戻をそろえる方向）
    assert stakes[3] > stakes[7]


# ---------------------------------------------------------------------------
# 5〜6. 予測本体の性質
# ---------------------------------------------------------------------------
def _fake_race(n_car: int):
    cars = list(range(1, n_car + 1))
    p3 = {c: 0.6 - 0.05 * i for i, c in enumerate(cars)}
    pw = {c: 0.30 - 0.03 * i for i, c in enumerate(cars)}
    meta = {
        c: {
            "race_point": 100.0 - i, "mark": (i + 1) if i < 4 else 0,
            "player_class": "S1" if i < 3 else "A1", "style": "逃" if i % 3 == 0 else "追",
            "line_group": 1 + i // 3, "line_size": 3, "line_pos": 1 + i % 3,
            "is_line_leader": 1 if i % 3 == 0 else 0,
            "first_rate": 10.0, "second_rate": 12.0, "third_rate": 15.0,
        }
        for i, c in enumerate(cars)
    }
    return cars, p3, pw, meta


@pytest.mark.parametrize("n_car", [7, 9])
def test_feature_matrix_shape_and_order(n_car):
    cars, p3, pw, meta = _fake_race(n_car)
    combos, X = op.build_race_features(cars, p3, pw, meta)
    assert len(combos) == len(list(itertools.combinations(cars, 3)))
    assert X.shape == (len(combos), len(op.FEATURE_NAMES))
    assert np.all(np.isfinite(X)), "特徴量に NaN/Inf があるとモデルが静かに壊れる"


def test_no_mark_maps_to_weakest_not_nan():
    """印なし(0)は NaN でなく最弱(5)へ。NaN にすると木が別枝へ落ちる。"""
    cars, p3, pw, meta = _fake_race(7)
    for c in cars:
        meta[c]["mark"] = 0
    _, X = op.build_race_features(cars, p3, pw, meta)
    for name in ("mk1", "mk2", "mk3"):
        col = X[:, op.FEATURE_NAMES.index(name)]
        assert np.all(col == op.NO_MARK)
    assert np.all(X[:, op.FEATURE_NAMES.index("n_marked")] == 0)


@pytest.mark.parametrize("n_car", [7, 9])
def test_missing_inputs_raise_unavailable(n_car):
    cars, p3, pw, meta = _fake_race(n_car)
    bad = dict(p3)
    bad[cars[0]] = 0.0
    with pytest.raises(op.OddsPredictionUnavailable):
        op.build_race_features(cars, bad, pw, meta)
    bad_meta = {c: dict(v) for c, v in meta.items()}
    bad_meta[cars[0]]["line_group"] = None
    with pytest.raises(op.OddsPredictionUnavailable):
        op.build_race_features(cars, p3, pw, bad_meta)


def test_unsupported_car_count_raises():
    cars, p3, pw, meta = _fake_race(7)
    with pytest.raises(op.OddsPredictionUnavailable):
        op.build_race_features(cars[:6], p3, pw, meta)


def test_meta_rejects_feature_mismatch(tmp_path, monkeypatch):
    """特徴量名が食い違ったら推論を拒否する（順序違いも含む）。"""
    path = tmp_path / "odds_trio_meta.json"
    path.write_text(json.dumps({
        "feature_names": list(op.FEATURE_NAMES)[:-1],
        "per_n_car": {"7": {"target_sum": 1.3455}},
    }), encoding="utf-8")
    monkeypatch.setattr(op, "META_PATH", path)
    monkeypatch.setattr(op, "_META_CACHE", None)
    with pytest.raises(op.OddsPredictionUnavailable, match="特徴量が一致しません"):
        op.load_meta()

    order_swapped = list(op.FEATURE_NAMES)
    order_swapped[0], order_swapped[1] = order_swapped[1], order_swapped[0]
    path.write_text(json.dumps({"feature_names": order_swapped, "per_n_car": {}}),
                    encoding="utf-8")
    monkeypatch.setattr(op, "_META_CACHE", None)
    with pytest.raises(op.OddsPredictionUnavailable, match="特徴量が一致しません"):
        op.load_meta()


def test_missing_meta_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(op, "META_PATH", tmp_path / "nope.json")
    monkeypatch.setattr(op, "_META_CACHE", None)
    with pytest.raises(op.OddsPredictionUnavailable):
        op.load_meta()


@pytest.mark.parametrize("n_car", [7, 9])
def test_predicted_board_is_coherent(n_car):
    """🔴 板として整合していること: Σ(1/オッズ) == 目標総和。

    これを外すと「配当を多めに配る」向きに壊れ、購入計画が崩れる。
    logMAE では検知できないのでここで固定する。
    """
    if not (op.MODEL_DIR / f"odds_trio_n{n_car}.txt").exists():
        pytest.skip("モデル未学習（scripts/train_odds_prediction.py）")
    cars, p3, pw, meta = _fake_race(n_car)
    board = op.predict_board(cars, p3, pw, meta)
    assert len(board) == len(list(itertools.combinations(cars, 3)))
    assert all(o > 0 for o in board.values())
    s = sum(1.0 / o for o in board.values())
    assert s == pytest.approx(op.target_sum(n_car), rel=1e-6)


@pytest.mark.parametrize("n_car", [7, 9])
def test_conservative_multiplier_is_below_one(n_car):
    """保守倍率は下側分位なので必ず 1 未満。1以上なら較正が壊れている。"""
    if not op.META_PATH.exists():
        pytest.skip("meta 未生成")
    for q in ("p05", "p10", "p25"):
        c = op.conservative_multiplier(n_car, q)
        assert 0 < c < 1.0, f"{n_car}車 {q} の保守倍率が {c}"
    assert (op.conservative_multiplier(n_car, "p05")
            < op.conservative_multiplier(n_car, "p10")
            < op.conservative_multiplier(n_car, "p25")), "分位の順序が壊れている"


def test_try_wrapper_never_raises(monkeypatch):
    """入稿を止めない。ただし黙って落ちないこと（呼び出し側は source で気づく）。"""
    def boom(*a, **k):
        raise RuntimeError("DB down")

    monkeypatch.setattr(op, "predicted_odds_for_legs", boom)
    assert op.try_predicted_odds_for_legs("20260811_13_01", 1, 2, [3, 4]) is None


# ---------------------------------------------------------------------------
# 7. 再構築経路には入れない（model-vintage look-ahead の予防）
# ---------------------------------------------------------------------------
def test_rebuild_stakes_does_not_use_predicted_odds():
    """`rebuild_stakes` が予測オッズを渡していないことを静的に確かめる。

    ここを通すと過去の再構築に未来のモデルが混ざる（本番だけ例外が出ない型の事故）。
    """
    import ast
    import inspect

    from src import rebuild_stakes

    tree = ast.parse(inspect.getsource(rebuild_stakes))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name == "tilted_stakes":
                kw = {k.arg for k in node.keywords}
                assert "predicted_odds" not in kw, (
                    "rebuild_stakes が予測オッズを使っている＝model-vintage look-ahead"
                )
    assert "odds_prediction" not in inspect.getsource(rebuild_stakes).replace(
        "src.odds_prediction` の予測オッズを渡してはいけない", ""
    ), "rebuild_stakes が odds_prediction を import している"
