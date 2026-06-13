"""race_selector.py のユニットテスト。

モデルファイル (chaos_classifier_v1.txt) の有無に依存しないテストと、
モデルが存在する場合のスモークテストを含む。
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.betting.race_selector import (
    FEATURES,
    RaceFeatures,
    _features_to_array,
    chaos_score,
    chaos_score_batch,
    features_from_dict,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

SAMPLE_FEATURES = RaceFeatures(
    head_count=16,
    distance=1600,
    is_turf=1,
    is_handicap=0,
    race_num=11,
    kai=1,
    day=2,
    grade_code=5,
    odds_top1=2.1,
    odds_top3_sum=7.5,
    odds_entropy=2.8,
    odds_gap12=1.5,
    odds_gap23=0.9,
    n_over10=8,
    wp_top1=0.28,
    wp_top3_sum=0.65,
    wp_entropy=2.3,
    wp_mkt_gap=1,
    wp_mkt_corr=0.8,
)

SAMPLE_FEATURES_WITH_MISSING = RaceFeatures(
    head_count=8,
    distance=2000,
    is_turf=0,
    is_handicap=1,
    race_num=3,
    kai=2,
    day=1,
    grade_code=3,
    # 市場特徴量: 欠損
    odds_top1=None,
    odds_top3_sum=None,
    odds_entropy=None,
    odds_gap12=None,
    odds_gap23=None,
    n_over10=None,
    # モデル特徴量: 一部欠損
    wp_top1=0.35,
    wp_top3_sum=None,
    wp_entropy=None,
    wp_mkt_gap=None,
    wp_mkt_corr=None,
)


# ---------------------------------------------------------------------------
# FEATURES 定数のテスト
# ---------------------------------------------------------------------------


class TestFeaturesConstant:
    """FEATURES リストの構造テスト。"""

    def test_features_length(self) -> None:
        """特徴量リストが19個であること。"""
        assert len(FEATURES) == 19

    def test_features_no_duplicate(self) -> None:
        """重複がないこと。"""
        assert len(FEATURES) == len(set(FEATURES))

    def test_features_all_strings(self) -> None:
        """全て文字列であること。"""
        assert all(isinstance(f, str) for f in FEATURES)

    def test_features_includes_required(self) -> None:
        """必須特徴量が含まれること。"""
        required = [
            "head_count",
            "is_turf",
            "is_handicap",
            "odds_top1",
            "odds_entropy",
            "wp_top1",
            "wp_entropy",
        ]
        for f in required:
            assert f in FEATURES, f"{f} が FEATURES に含まれていない"


# ---------------------------------------------------------------------------
# RaceFeatures のテスト
# ---------------------------------------------------------------------------


class TestRaceFeatures:
    """RaceFeatures データクラスのテスト。"""

    def test_instantiation(self) -> None:
        """正常なインスタンス生成。"""
        f = SAMPLE_FEATURES
        assert f.head_count == 16
        assert f.is_turf == 1
        assert f.is_handicap == 0
        assert f.odds_top1 == 2.1

    def test_none_fields_accepted(self) -> None:
        """None フィールドが受け入れられること。"""
        f = SAMPLE_FEATURES_WITH_MISSING
        assert f.odds_top1 is None
        assert f.wp_mkt_corr is None

    def test_features_from_dict_full(self) -> None:
        """features_from_dict が正しく変換すること。"""
        d = {
            "head_count": "16",
            "distance": "1600",
            "is_turf": "1",
            "is_handicap": "0",
            "race_num": "11",
            "kai": "1",
            "day": "2",
            "grade_code": "5",
            "odds_top1": "2.1",
            "odds_top3_sum": "7.5",
            "odds_entropy": "2.8",
            "odds_gap12": "1.5",
            "odds_gap23": "0.9",
            "n_over10": "8",
            "wp_top1": "0.28",
            "wp_top3_sum": "0.65",
            "wp_entropy": "2.3",
            "wp_mkt_gap": "1",
            "wp_mkt_corr": "0.8",
        }
        f = features_from_dict(d)
        assert f.head_count == 16
        assert f.distance == 1600.0
        assert f.odds_top1 == pytest.approx(2.1)
        assert f.is_turf == 1  # int に変換されること

    def test_features_from_dict_missing(self) -> None:
        """辞書に存在しないキーは None になること。"""
        d = {"head_count": 8, "distance": 2000}
        f = features_from_dict(d)
        assert f.odds_top1 is None
        assert f.wp_entropy is None

    def test_features_from_dict_empty(self) -> None:
        """空辞書は全フィールド None。"""
        f = features_from_dict({})
        for fname in FEATURES:
            v = getattr(f, fname)
            assert v is None, f"{fname} は None であるべきだが {v}"


# ---------------------------------------------------------------------------
# _features_to_array のテスト
# ---------------------------------------------------------------------------


class TestFeaturesToArray:
    """_features_to_array 変換関数のテスト。"""

    def test_output_shape(self) -> None:
        """出力は len(FEATURES) の 1D 配列。"""
        arr = _features_to_array(SAMPLE_FEATURES)
        assert arr.shape == (len(FEATURES),)

    def test_output_dtype(self) -> None:
        """出力は float64。"""
        arr = _features_to_array(SAMPLE_FEATURES)
        assert arr.dtype == np.float64

    def test_none_becomes_nan(self) -> None:
        """None フィールドは NaN に変換される。"""
        arr = _features_to_array(SAMPLE_FEATURES_WITH_MISSING)
        # odds_top1 は None → NaN
        idx_odds_top1 = FEATURES.index("odds_top1")
        assert math.isnan(arr[idx_odds_top1])

    def test_values_preserved(self) -> None:
        """数値フィールドが正しく変換される。"""
        arr = _features_to_array(SAMPLE_FEATURES)
        idx_head = FEATURES.index("head_count")
        idx_dist = FEATURES.index("distance")
        idx_odds = FEATURES.index("odds_top1")
        assert arr[idx_head] == pytest.approx(16.0)
        assert arr[idx_dist] == pytest.approx(1600.0)
        assert arr[idx_odds] == pytest.approx(2.1)

    def test_full_missing_features(self) -> None:
        """全フィールド None の場合、全て NaN。"""
        f = features_from_dict({})
        arr = _features_to_array(f)
        assert arr.shape == (len(FEATURES),)
        assert all(math.isnan(v) for v in arr)


# ---------------------------------------------------------------------------
# chaos_score のモックテスト（モデルファイル不要）
# ---------------------------------------------------------------------------


class TestChaoScoreWithMock:
    """モデルをモックした chaos_score テスト。"""

    def test_returns_float(self) -> None:
        """chaos_score が float を返すこと。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([0.42])

        with patch("src.betting.race_selector._get_model", return_value=mock_model):
            score = chaos_score(SAMPLE_FEATURES)

        assert isinstance(score, float)
        assert score == pytest.approx(0.42)

    def test_score_range(self) -> None:
        """スコアは 0〜1 の範囲を返すこと（モックで確認）。"""
        for expected in [0.0, 0.5, 1.0]:
            mock_model = MagicMock()
            mock_model.predict.return_value = np.array([expected])
            with patch("src.betting.race_selector._get_model", return_value=mock_model):
                score = chaos_score(SAMPLE_FEATURES)
            assert score == pytest.approx(expected)

    def test_predict_called_with_correct_shape(self) -> None:
        """model.predict に (1, 19) 形状の配列が渡されること。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([0.3])

        with patch("src.betting.race_selector._get_model", return_value=mock_model):
            chaos_score(SAMPLE_FEATURES)

        call_args = mock_model.predict.call_args
        X = call_args[0][0]
        assert X.shape == (1, len(FEATURES)), f"期待 (1, {len(FEATURES)}), 実際 {X.shape}"

    def test_missing_features_passed_as_nan(self) -> None:
        """欠損フィールドが NaN として predict に渡されること。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([0.2])

        with patch("src.betting.race_selector._get_model", return_value=mock_model):
            chaos_score(SAMPLE_FEATURES_WITH_MISSING)

        call_args = mock_model.predict.call_args
        X = call_args[0][0]
        idx_odds_top1 = FEATURES.index("odds_top1")
        assert math.isnan(X[0, idx_odds_top1])


# ---------------------------------------------------------------------------
# chaos_score_batch のモックテスト
# ---------------------------------------------------------------------------


class TestChaosScoreBatch:
    """chaos_score_batch のテスト。"""

    def test_empty_list(self) -> None:
        """空リストは空配列を返す。"""
        result = chaos_score_batch([])
        assert isinstance(result, np.ndarray)
        assert len(result) == 0

    def test_batch_returns_correct_length(self) -> None:
        """バッチサイズと出力長が一致すること。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([0.1, 0.5, 0.9])

        features_list = [SAMPLE_FEATURES, SAMPLE_FEATURES_WITH_MISSING, SAMPLE_FEATURES]
        with patch("src.betting.race_selector._get_model", return_value=mock_model):
            result = chaos_score_batch(features_list)

        assert len(result) == 3

    def test_batch_matrix_shape(self) -> None:
        """predict に (n, 19) 形状の配列が渡されること。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([0.1, 0.2])

        features_list = [SAMPLE_FEATURES, SAMPLE_FEATURES]
        with patch("src.betting.race_selector._get_model", return_value=mock_model):
            chaos_score_batch(features_list)

        call_args = mock_model.predict.call_args
        X = call_args[0][0]
        assert X.shape == (2, len(FEATURES))


# ---------------------------------------------------------------------------
# モデルファイルが存在する場合のスモークテスト
# ---------------------------------------------------------------------------


_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "chaos_classifier_v1.txt"


@pytest.mark.skipif(
    not _MODEL_PATH.exists(),
    reason="chaos_classifier_v1.txt が存在しない（学習前）",
)
class TestWithRealModel:
    """実際のモデルファイルを使ったスモークテスト。"""

    def test_score_is_float_in_range(self) -> None:
        """スコアが 0〜1 の float であること。"""
        import src.betting.race_selector as sel

        # キャッシュリセット
        sel._cached_model = None
        score = chaos_score(SAMPLE_FEATURES)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0, f"スコアが範囲外: {score}"
        # キャッシュリセット（テスト後）
        sel._cached_model = None

    def test_batch_consistent_with_single(self) -> None:
        """バッチ推論と単一推論の結果が一致すること。"""
        import src.betting.race_selector as sel

        sel._cached_model = None

        score_single = chaos_score(SAMPLE_FEATURES)
        sel._cached_model = None  # キャッシュリセット

        scores_batch = chaos_score_batch([SAMPLE_FEATURES])
        sel._cached_model = None

        assert len(scores_batch) == 1
        assert scores_batch[0] == pytest.approx(score_single, abs=1e-6)
