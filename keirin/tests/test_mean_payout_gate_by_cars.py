"""平均払戻ゲートの車数依存（2026-08-25 新設）。

🔴 **7車では切る向きが正しく、9車では逆**という実測に基づく分岐なので、
   ここが壊れると9車の一番良い帯（高信頼＝安い配当）を毎日捨てることになる。
   根拠の数値は `src/stake_allocation.mean_payout_gate_applies` のコメント。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.stake_allocation import (  # noqa: E402
    MEAN_PAYOUT_GATE_CONF_MAX_9CAR, MIN_MEAN_PAYOUT, mean_payout_gate_applies,
)


def test_7car_always_gated():
    """7車は従来どおり無条件で掛ける（信頼度が読めなくても掛ける）。"""
    assert mean_payout_gate_applies(7, None) is True
    assert mean_payout_gate_applies(7, 0.5) is True
    assert mean_payout_gate_applies(7, 1.9) is True


def test_9car_gated_only_when_low_confidence():
    """9車は低信頼のときだけ。高信頼の安いレースは**最良の帯**なので残す。"""
    assert mean_payout_gate_applies(9, 1.10) is True
    assert mean_payout_gate_applies(9, MEAN_PAYOUT_GATE_CONF_MAX_9CAR - 0.01) is True
    assert mean_payout_gate_applies(9, MEAN_PAYOUT_GATE_CONF_MAX_9CAR) is False
    assert mean_payout_gate_applies(9, 1.45) is False


def test_9car_not_gated_when_confidence_unknown():
    """🔴 判定に必要な値が無ければ**掛けない**（出す側へ倒す）。

    分からないことを理由に商品を落とさない、という他ゲートと同じ思想。
    9車では「掛けない」が実測でも良い側（ROI 84.1% ↔ 75.7%）。
    """
    assert mean_payout_gate_applies(9, None) is False


def test_threshold_unchanged():
    """金額の下限そのものは動かしていない（変えたのは掛ける条件だけ）。"""
    assert MIN_MEAN_PAYOUT == 20_000


def test_gate_threshold_shown_as_63_percent():
    """🔴 ゲートの閾値と画面の信頼度は**同じ量・同じ丸め**でなければならない。

    9車の 1.25 は表示上 63%。ここがずれると「なぜ出ないのか」が読めなくなる。

    🔴 **`round()` で書かないこと。** Python の `round(62.5)` は偶数丸めで 62 に
       なり、ユーザー指定の四捨五入（63）と食い違う。実際この検査を `round` で
       書いたら落ちた。正本 `confidence_pct` は `floor(x + 0.5)` を使っている。
    """
    import math

    from src.p3_calibration import CONFIDENCE_FULL_SUM

    pct = 100.0 * MEAN_PAYOUT_GATE_CONF_MAX_9CAR / CONFIDENCE_FULL_SUM
    assert math.floor(pct + 0.5) == 63
    assert round(pct) == 62          # 偶数丸めだとこうなる（使ってはいけない）


def test_confidence_pct_is_half_up_and_bounded():
    """信頼度は 0〜100 に収まり、上限（合計2.00）でちょうど 100% になる。"""
    from src.p3_calibration import confidence_pct

    assert confidence_pct({1: 1.0, 2: 1.0, 3: 0.0}) == 100
    assert confidence_pct({1: 0.0, 2: 0.0, 3: 0.0}) == 0
    assert confidence_pct({1: 0.5}) is None       # 2車に満たない
