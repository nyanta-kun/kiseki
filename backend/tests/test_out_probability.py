"""着外率（6着以下確率）ヘッド ユニットテスト

Web の足切り（グレーアウト）判定を総合指数のトップ差から着外率へ置き換えたことに伴うテスト。
検証根拠: memory/jra_out_rate_3head_verification_2026_08_02.md
DB 接続不要（モデルファイルの有無に依存する項目は skip する）。
"""

from __future__ import annotations

import numpy as np
import pytest

from src.indices.composite import (
    _OUT_PROB_MODEL_PATH,
    _V26_FEATURE_NAMES,
    OUT_PROB_CUTOFF,
    OUT_PROB_FEATURE_NAMES,
    _load_out_prob_model,
)


class TestOutProbContract:
    """特徴量契約・閾値の不変条件。"""

    def test_features_match_v26(self) -> None:
        """着外率ヘッドの特徴量は v26 と同一列・同順（推論時に X_v26 を共用するため）"""
        assert OUT_PROB_FEATURE_NAMES == _V26_FEATURE_NAMES

    def test_feature_count(self) -> None:
        """sub-indices 17 + race meta 10 + horse meta 7 = 34 列"""
        assert len(OUT_PROB_FEATURE_NAMES) == 34

    def test_no_odds_features(self) -> None:
        """オッズ・人気は特徴量に含めない（発走前確定情報のみで判定する）"""
        forbidden = ("odds", "popularity", "ninki")
        for name in OUT_PROB_FEATURE_NAMES:
            assert not any(f in name.lower() for f in forbidden), name

    def test_cutoff_threshold(self) -> None:
        """足切り閾値は 0.80（除外30% / 1着取りこぼし5.0% の検証値）"""
        assert OUT_PROB_CUTOFF == 0.80
        assert 0.0 < OUT_PROB_CUTOFF < 1.0


@pytest.mark.skipif(
    not _OUT_PROB_MODEL_PATH.exists(),
    reason="models/jra_out_rate_lgb.txt が未生成（scripts/train_jra_out_rate.py で作成）",
)
class TestOutProbModel:
    """学習済みモデルの推論契約。"""

    def test_model_loads(self) -> None:
        """モデルが読み込め、期待する特徴量数を持つ"""
        model = _load_out_prob_model()
        assert model is not None
        assert model.num_feature() == len(OUT_PROB_FEATURE_NAMES)

    def test_predict_returns_probabilities(self) -> None:
        """出力は [0, 1] の確率で、頭数分returnされる"""
        model = _load_out_prob_model()
        assert model is not None
        X = np.full((5, len(OUT_PROB_FEATURE_NAMES)), 50.0, dtype=float)
        preds = np.asarray(model.predict(X), dtype=float)
        assert preds.shape == (5,)
        assert np.all(preds >= 0.0) and np.all(preds <= 1.0)

    def test_stronger_horse_has_lower_out_prob(self) -> None:
        """全サブ指数が高い馬は、低い馬より着外率が低い"""
        model = _load_out_prob_model()
        assert model is not None
        n = len(OUT_PROB_FEATURE_NAMES)
        strong = np.full((1, n), 50.0)
        weak = np.full((1, n), 50.0)
        strong[0, :17] = 75.0  # sub-indices を高く
        weak[0, :17] = 25.0
        # レースメタ（distance, head_count）は同条件に揃える
        for row in (strong, weak):
            row[0, 17] = 1600.0
            row[0, 18] = 16.0
        p_strong = float(model.predict(strong)[0])
        p_weak = float(model.predict(weak)[0])
        assert p_strong < p_weak
