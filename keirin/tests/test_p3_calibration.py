"""3着内率の後段較正（`src/p3_calibration.py`）の回帰テスト。

固定するのは「壊れても例外が出ない」不変条件だけ:

1. 🔴 **単調変換であること**＝レース内の順位を変えない。ここが崩れると
   軸選定が静かに変わる（較正はゲートだけを直すための仕組み）
2. 🔴 **「準決勝」を「決勝」と誤判定しないこと**（部分一致で当たる）。
   誤判定すると準決勝が決勝の強い補正(a=0.874)を受けて母集団が壊れる
3. 🔴 **`cup_grade` が None のとき F級へ倒すこと**。2024年以前は NULL なので、
   上位側へ倒すと過去の再構築でゲートが不当に厳しくなる
4. **`pred_top3_pct` を書き換えないこと**（表示・他ランクへ波及させない）
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.p3_calibration import (  # noqa: E402
    calibrate_top3, calibrated_p3_sum_top2, coefficients, grade_group, race_type_group,
)


def test_monotonic_within_a_race():
    """🔴 順位が絶対に変わらないこと（全セグメントで確認）。"""
    random.seed(0)
    probs = sorted((random.random() for _ in range(9)), reverse=True)
    for rt in ("決勝", "S級準決勝", "予選", "特選", None):
        for g in (None, 1, 2, 3, 4, 5, 6):
            cal = [calibrate_top3(p, rt, g) for p in probs]
            assert cal == sorted(cal, reverse=True), f"{rt}/{g} で順位が入れ替わった"


def test_semifinal_is_not_treated_as_final():
    """🔴 「準決勝」は「決勝」に部分一致するので、除外を先に見ること。"""
    assert race_type_group("準決勝") == "準決勝"
    assert race_type_group("S級準決勝") == "準決勝"
    assert race_type_group("チャレンジ準決勝") == "準決勝"
    assert race_type_group("決勝") == "決勝"
    assert race_type_group("ガールズ決勝") == "決勝"
    assert coefficients("準決勝", 1) != coefficients("決勝", 1)


def test_unknown_grade_falls_back_to_f_class():
    """🔴 cup_grade が None なら F級（補正ほぼ恒等）。上位へ倒さないこと。"""
    assert grade_group(None) == "F級"
    assert coefficients("予選", None) == coefficients("予選", 1)
    # F級の補正はほぼ恒等（過去分の再構築を静かに壊さない）
    a, b = coefficients("予選", None)
    assert 0.95 <= a <= 1.05 and abs(b) <= 0.10


def test_grade_groups():
    assert grade_group(6) == "上位" and grade_group(5) == "上位" and grade_group(4) == "上位"
    assert grade_group(3) == "GIII"
    assert grade_group(2) == "F級" and grade_group(1) == "F級"


def test_final_and_high_grade_are_compressed():
    """決勝・上位グレードでは a<1（分散を縮める）＝過大評価を潰す向き。"""
    assert coefficients("決勝", 1)[0] < 0.95, "決勝の圧縮が効いていない"
    assert coefficients("予選", 5)[0] < 0.95, "上位グレードの圧縮が効いていない"
    # 平場は触らない
    assert 0.95 <= coefficients("予選", 1)[0] <= 1.05


def test_calibration_lowers_high_probabilities_in_finals():
    """決勝では上位帯を押し下げること（ずれが上位帯に集中していたため）。"""
    for p in (0.5, 0.6, 0.7, 0.8):
        assert calibrate_top3(p, "決勝", 1) < p
    # 下位帯はほぼ動かさない
    assert abs(calibrate_top3(0.10, "決勝", 1) - 0.10) < 0.03


def test_sum_top2_uses_two_highest_after_calibration():
    probs = {1: 0.80, 2: 0.70, 3: 0.50, 4: 0.20}
    got = calibrated_p3_sum_top2(probs, "予選", 1)
    exp = (calibrate_top3(0.80, "予選", 1) + calibrate_top3(0.70, "予選", 1))
    assert abs(got - exp) < 1e-9


def test_sum_top2_handles_degenerate_input():
    assert calibrated_p3_sum_top2({}, "予選", 1) is None
    assert calibrated_p3_sum_top2({1: 0.5}, "予選", 1) is None


def test_edge_probabilities_do_not_blow_up():
    for p in (0.0, 1.0, 1e-9, 1 - 1e-9):
        for rt in ("決勝", "予選"):
            v = calibrate_top3(p, rt, 5)
            assert 0.0 <= v <= 1.0


def test_gate_helper_falls_back_to_raw_sum():
    """🔴 較正値を持たない候補（旧JSON）は生の値へ落ちる。0にしないこと。

    0 にすると全レースがゲートに落ちて**商品が全滅する**。
    """
    from src.strategy_wt import _gate_p3_sum

    assert _gate_p3_sum({"p3_sum_top2": 1.50, "p3_sum_top2_cal": 1.40}) == 1.40
    assert _gate_p3_sum({"p3_sum_top2": 1.50}) == 1.50          # 旧JSON
    assert _gate_p3_sum({"p3_sum_top2": 1.50, "p3_sum_top2_cal": None}) == 1.50
    assert _gate_p3_sum({}) == 0.0                               # 値が無ければ落とす


def test_pred_top3_pct_is_not_rewritten_anywhere():
    """🔴 較正値を `pred_top3_pct` へ書き戻していないこと（表示・特徴量への波及禁止）。

    `race_point` を表示用の値で上書きして特徴量を汚染した事故と同じ轍。
    """
    for name in ("src/cli/main.py", "scripts/backfill_7c_rank_wt.py",
                 "scripts/backfill_9c_rank_wt.py"):
        src = (REPO / name).read_text(encoding="utf-8")
        for line in src.splitlines():
            if "calibrate" in line and "pred_top3_pct" in line and "=" in line:
                raise AssertionError(f"{name}: 較正値を pred_top3_pct へ入れている: {line}")
