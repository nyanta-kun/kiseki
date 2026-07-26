"""v26 Phase1 新規特徴量（研究用スクリプト）ユニットテスト

DB接続不要。純粋な計算式（compute_pci / shrink_rate）と、
小さな合成 DataFrame を用いた point-in-time 漏れ（未来データ混入）が
無いことの検証を行う。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.train_v26_phase1_features import (
    K_SHRINK,
    attach_phase1_features,
    compute_pci,
    shrink_rate,
)

# ---------------------------------------------------------------------------
# compute_pci
# ---------------------------------------------------------------------------


class TestComputePci:
    """PCI計算式の単体テスト。"""

    def test_basic_calculation(self) -> None:
        """distance=1800, last_3f=33.0, finish_time=116.0 の手計算検証。"""
        # rest_time = 116-33=83, rest_per_3f = 83/((1800-600)/600)=41.5
        # pci = 41.5/33*100-50
        expected = 41.5 / 33 * 100 - 50
        assert compute_pci(116.0, 33.0, 1800) == pytest.approx(expected)

    def test_distance_at_or_below_600_returns_none(self) -> None:
        """distance<=600 は None。"""
        assert compute_pci(60.0, 33.0, 600) is None
        assert compute_pci(60.0, 33.0, 500) is None

    def test_last_3f_none_returns_none(self) -> None:
        """last_3f が None は None。"""
        assert compute_pci(116.0, None, 1800) is None

    def test_last_3f_zero_returns_none(self) -> None:
        """last_3f=0 は None（ゼロ割回避）。"""
        assert compute_pci(116.0, 0.0, 1800) is None

    def test_finish_time_none_returns_none(self) -> None:
        """finish_time が None は None。"""
        assert compute_pci(None, 33.0, 1800) is None

    def test_distance_none_returns_none(self) -> None:
        """distance が None は None。"""
        assert compute_pci(116.0, 33.0, None) is None

    def test_nan_inputs_return_none(self) -> None:
        """NaN 入力も None 扱い。"""
        assert compute_pci(np.nan, 33.0, 1800) is None
        assert compute_pci(116.0, np.nan, 1800) is None
        assert compute_pci(116.0, 33.0, np.nan) is None


# ---------------------------------------------------------------------------
# shrink_rate
# ---------------------------------------------------------------------------


class TestShrinkRate:
    """ベイズ縮小式の単体テスト。"""

    def test_zero_n_returns_global_rate(self) -> None:
        """n=0 は global_rate をそのまま返す。"""
        assert shrink_rate(0, None, 0.35) == 0.35
        assert shrink_rate(0, 0.9, 0.35) == 0.35

    def test_none_rate_returns_global_rate(self) -> None:
        """rate が None の場合は global_rate。"""
        assert shrink_rate(10, None, 0.35) == 0.35

    def test_nan_rate_returns_global_rate(self) -> None:
        """rate が NaN の場合は global_rate。"""
        assert shrink_rate(10, float("nan"), 0.35) == 0.35

    def test_large_n_approaches_raw_rate(self) -> None:
        """n が k(=K_SHRINK)よりずっと大きい場合、縮小後の値は生レートに近づく。"""
        result = shrink_rate(100_000, 0.9, 0.35, k=K_SHRINK)
        assert result == pytest.approx(0.9, abs=0.01)

    def test_small_n_blends_toward_global(self) -> None:
        """n が小さい場合、生レートと global_rate の中間に縮小される。"""
        result = shrink_rate(5, 1.0, 0.35, k=20)
        expected = (5 * 1.0 + 20 * 0.35) / (5 + 20)
        assert result == pytest.approx(expected)
        # 生レート(1.0)より小さく、global_rate(0.35)より大きい
        assert 0.35 < result < 1.0

    def test_n_equals_k_gives_midpoint_weighting(self) -> None:
        """n=kのとき、生レートとglobal_rateの単純平均相当になる。"""
        result = shrink_rate(20, 0.8, 0.4, k=20)
        assert result == pytest.approx((0.8 + 0.4) / 2)


# ---------------------------------------------------------------------------
# attach_phase1_features: point-in-time 漏れ検証（合成データ）
# ---------------------------------------------------------------------------


_HIST_COLUMNS = [
    "race_id", "horse_id", "date", "finish_position", "passing_4", "last_3f",
    "finish_time", "horse_weight", "distance", "jockey_id", "trainer_id",
    "sire", "sire_of_dam", "speed_index",
]
_TARGET_COLUMNS = [
    "race_id", "horse_id", "race_date", "horse_weight", "jockey_id", "trainer_id",
    "sire", "sire_of_dam",
]


def _make_history(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.columns = _HIST_COLUMNS
    return df


def _make_target(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.columns = _TARGET_COLUMNS
    return df


class TestNoFutureLeakage:
    """将来レースを追加しても対象日以前の特徴量が変化しないことを検証する。"""

    def _base_history(self) -> pd.DataFrame:
        return _make_history([
            [100, 1, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
            [101, 1, 20230201, 2, 3, 34.0, 118.0, 478, 1800, 10, 20, "SireA", "DamSireA", 55.0],
            [102, 1, 20230301, 1, 1, 33.0, 116.0, 482, 1800, 11, 21, "SireA", "DamSireA", 60.0],
            [102, 2, 20230301, 3, 4, 34.5, 117.0, 450, 1800, 12, 22, "SireB", "DamSireB", 52.0],
            [90, 2, 20221201, 1, 1, 33.5, 115.0, 448, 1800, 12, 22, "SireB", "DamSireB", 58.0],
            [95, 2, 20221215, 2, 2, 34.0, 116.0, 449, 1800, 12, 22, "SireB", "DamSireB", 56.0],
        ])

    def _target(self) -> pd.DataFrame:
        return _make_target([[200, 1, 20230401, 480, 10, 20, "SireA", "DamSireA"]])

    def test_future_race_does_not_change_features(self) -> None:
        """対象レース日より後の極端なレースを追加しても結果が一切変わらない。"""
        history = self._base_history()
        target = self._target()
        out_before = attach_phase1_features(target, history)

        future_rows = pd.DataFrame([
            [300, 1, 20230501, 18, 18, 50.0, 200.0, 300, 1800, 10, 20, "SireA", "DamSireA", 1.0],
            [301, 2, 20230501, 1, 1, 20.0, 80.0, 999, 1800, 999, 999, "SireC", "DamSireC", 999.0],
        ])
        future_rows.columns = _HIST_COLUMNS
        history_with_future = pd.concat([history, future_rows], ignore_index=True)

        out_after = attach_phase1_features(target, history_with_future)

        new_cols = [
            "corner_stretch_regression", "bounce_score", "pace_index_pci",
            "collateral_form", "nicks_score", "peak_weight_proximity",
            "jockey_trainer_combo",
        ]
        pd.testing.assert_frame_equal(out_before[new_cols], out_after[new_cols])

    def test_same_day_race_is_excluded(self) -> None:
        """target と同日(race_date と同じ日付)のレースは merge_asof の
        allow_exact_matches=False により除外され、結果に影響しない。"""
        history = self._base_history()
        target = self._target()  # race_date=20230401

        out_without_sameday = attach_phase1_features(target, history)

        sameday_row = pd.DataFrame([
            [400, 1, 20230401, 1, 1, 10.0, 50.0, 999, 1800, 10, 20, "SireA", "DamSireA", 999.0],
        ])
        sameday_row.columns = _HIST_COLUMNS
        history_with_sameday = pd.concat([history, sameday_row], ignore_index=True)
        out_with_sameday = attach_phase1_features(target, history_with_sameday)

        new_cols = ["corner_stretch_regression", "bounce_score", "pace_index_pci"]
        pd.testing.assert_frame_equal(
            out_without_sameday[new_cols], out_with_sameday[new_cols]
        )


class TestCornerStretchRegression:
    """corner_stretch_regression の値検証。"""

    def test_last_three_races_average(self) -> None:
        """直近3走の (passing_4 - finish_position) の単純平均。"""
        history = _make_history([
            [100, 1, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
            [101, 1, 20230201, 2, 3, 34.0, 118.0, 478, 1800, 10, 20, "SireA", "DamSireA", 55.0],
            [102, 1, 20230301, 1, 1, 33.0, 116.0, 482, 1800, 11, 21, "SireA", "DamSireA", 60.0],
        ])
        target = _make_target([[200, 1, 20230401, 480, 10, 20, "SireA", "DamSireA"]])
        out = attach_phase1_features(target, history)
        # (8-5)=3, (3-2)=1, (1-1)=0 -> mean=1.333...
        assert out["corner_stretch_regression"].iloc[0] == pytest.approx((3 + 1 + 0) / 3)

    def test_no_history_defaults_to_zero(self) -> None:
        """過去レースが無い馬は0.0で埋める。"""
        history = _make_history([
            [100, 2, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
        ])
        target = _make_target([[200, 999, 20230401, 480, 10, 20, "SireA", "DamSireA"]])
        out = attach_phase1_features(target, history)
        assert out["corner_stretch_regression"].iloc[0] == 0.0


class TestBounceScore:
    """bounce_score の値検証。"""

    def test_insufficient_history_defaults_to_zero(self) -> None:
        """直近4走に満たない場合は0.0。"""
        history = _make_history([
            [100, 1, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
            [101, 1, 20230201, 2, 3, 34.0, 118.0, 478, 1800, 10, 20, "SireA", "DamSireA", 55.0],
            [102, 1, 20230301, 1, 1, 33.0, 116.0, 482, 1800, 11, 21, "SireA", "DamSireA", 60.0],
        ])
        target = _make_target([[200, 1, 20230401, 480, 10, 20, "SireA", "DamSireA"]])
        out = attach_phase1_features(target, history)
        assert out["bounce_score"].iloc[0] == 0.0

    def test_four_races_computes_bounce(self) -> None:
        """4走揃うと前走 - 前3走平均 を計算する。"""
        history = _make_history([
            [99, 1, 20221201, 4, 6, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 40.0],
            [100, 1, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
            [101, 1, 20230201, 2, 3, 34.0, 118.0, 478, 1800, 10, 20, "SireA", "DamSireA", 45.0],
            [102, 1, 20230301, 1, 1, 33.0, 116.0, 482, 1800, 11, 21, "SireA", "DamSireA", 70.0],
        ])
        target = _make_target([[200, 1, 20230401, 480, 10, 20, "SireA", "DamSireA"]])
        out = attach_phase1_features(target, history)
        # 前走(70.0) - 前3走平均((40+50+45)/3=45.0) = 25.0
        assert out["bounce_score"].iloc[0] == pytest.approx(70.0 - (40.0 + 50.0 + 45.0) / 3)


class TestPeakWeightProximity:
    """peak_weight_proximity の値検証。"""

    def test_uses_best_finish_weight(self) -> None:
        """自己ベスト着順時の体重との差分（負値）を返す。"""
        history = _make_history([
            [100, 1, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
            [101, 1, 20230201, 1, 3, 34.0, 118.0, 470, 1800, 10, 20, "SireA", "DamSireA", 55.0],  # best
            [102, 1, 20230301, 3, 1, 33.0, 116.0, 482, 1800, 11, 21, "SireA", "DamSireA", 60.0],
        ])
        target = _make_target([[200, 1, 20230401, 480, 10, 20, "SireA", "DamSireA"]])
        out = attach_phase1_features(target, history)
        assert out["peak_weight_proximity"].iloc[0] == pytest.approx(-abs(480 - 470))

    def test_no_history_defaults_to_zero(self) -> None:
        """過去実績なしは0.0。"""
        history = _make_history([
            [100, 2, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
        ])
        target = _make_target([[200, 999, 20230401, 480, 10, 20, "SireA", "DamSireA"]])
        out = attach_phase1_features(target, history)
        assert out["peak_weight_proximity"].iloc[0] == 0.0


class TestCollateralForm:
    """collateral_form の値検証。"""

    def test_no_opponent_data_defaults_neutral(self) -> None:
        """対戦相手の情報が取れない場合は0.5(中立)。"""
        history = _make_history([
            [100, 1, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
        ])
        target = _make_target([[200, 1, 20230401, 480, 10, 20, "SireA", "DamSireA"]])
        out = attach_phase1_features(target, history)
        assert out["collateral_form"].iloc[0] == pytest.approx(0.5)


class TestNicksAndJockeyTrainerCombo:
    """nicks_score / jockey_trainer_combo のシュリンク挙動検証。"""

    def test_no_pair_data_falls_back_to_global_rate(self) -> None:
        """馬自身しかいないペアはn=0となりglobal_rateへフォールバックする。"""
        history = _make_history([
            [100, 1, 20230101, 5, 8, 35.0, 120.0, 480, 1800, 10, 20, "SireA", "DamSireA", 50.0],
            [101, 1, 20230201, 2, 3, 34.0, 118.0, 478, 1800, 10, 20, "SireA", "DamSireA", 55.0],
        ])
        target = _make_target([[200, 1, 20230401, 480, 10, 20, "SireA", "DamSireA"]])
        out = attach_phase1_features(target, history)
        # global_rate = (0+1)/2 = 0.5 のはず（fp=5→0, fp=2→1）
        assert out["nicks_score"].iloc[0] == pytest.approx(0.5)
        assert out["jockey_trainer_combo"].iloc[0] == pytest.approx(0.5)
