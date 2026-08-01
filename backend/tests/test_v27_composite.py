"""v27 総合指数（順位回帰 + 着外率合成）ユニットテスト

検証根拠: memory/jra_rank_quality_redesign_2026_08_02.md
DB 接続不要（モデルファイルの有無に依存する項目は skip する）。
"""

from __future__ import annotations

import numpy as np
import pytest

from src.indices.composite import (
    _REG_RANK_MODEL_PATH,
    COMPOSITE_VERSION,
    OUT_PROB_FEATURE_NAMES,
    V27_OUT_WEIGHT,
    _load_reg_rank_model,
    _zscore,
    blend_v27,
)


class TestZscore:
    """レース内標準化。"""

    def test_mean_zero_std_one(self) -> None:
        z = _zscore(np.array([10.0, 20.0, 30.0, 40.0]))
        assert float(z.mean()) == pytest.approx(0.0, abs=1e-9)
        assert float(z.std()) == pytest.approx(1.0, abs=1e-9)

    def test_constant_input_returns_zeros(self) -> None:
        """全馬同値（分散ゼロ）でもゼロ除算せず 0 を返す"""
        z = _zscore(np.array([5.0, 5.0, 5.0]))
        assert np.allclose(z, 0.0)

    def test_empty(self) -> None:
        assert len(_zscore(np.array([]))) == 0


class TestBlendV27:
    """composite_index の合成。"""

    def test_range_clipped_0_100(self) -> None:
        """出力は 0〜100 に収まる"""
        out = blend_v27([0.1, 0.3, 0.5, 0.9], [0.4, 0.6, 0.7, 0.95])
        assert min(out) >= 0.0
        assert max(out) <= 100.0

    def test_dispersion_tracks_race_spread(self) -> None:
        """並びが明確なレースほど指数の幅が広くなる（混戦度が confidence に伝わる）

        min-max で 15〜85 に固定すると全レースが同じ幅になり
        `calculate_race_confidence` の分散スコアが機能しなくなるため、
        レース内のばらつきが保存されることを保証する。
        """
        clear = blend_v27([0.10, 0.50, 0.90], None)   # よく分離したレース
        muddy = blend_v27([0.45, 0.50, 0.55], None)   # 混戦
        assert (max(clear) - min(clear)) > (max(muddy) - min(muddy)) * 2

    def test_lower_reg_rank_is_higher_index(self) -> None:
        """reg_rank は小さいほど上位 → composite は大きくなる"""
        out = blend_v27([0.1, 0.5, 0.9], None)
        assert out[0] > out[1] > out[2]

    def test_out_prob_pushes_down(self) -> None:
        """同じ reg_rank なら着外率が高い馬ほど composite が低い"""
        out = blend_v27([0.5, 0.5, 0.5], [0.2, 0.5, 0.9])
        assert out[0] > out[1] > out[2]

    def test_out_prob_none_is_reg_rank_only(self) -> None:
        """着外率が無い場合は reg_rank 単体の順位になる"""
        a = blend_v27([0.2, 0.4, 0.8], None)
        b = blend_v27([0.2, 0.4, 0.8], [0.5, 0.5, 0.5])  # 定数なら z=0 で同じ
        assert a == b

    def test_length_mismatch_ignores_out_prob(self) -> None:
        """長さ不一致の着外率は無視され reg_rank 単体にフォールバックする"""
        a = blend_v27([0.2, 0.4, 0.8], [0.1, 0.9])
        b = blend_v27([0.2, 0.4, 0.8], None)
        assert a == b

    def test_empty(self) -> None:
        assert blend_v27([], []) == []

    def test_single_horse(self) -> None:
        """1頭立ては 50.0（min-max 不能）"""
        assert blend_v27([0.5], [0.5]) == [50.0]

    def test_all_equal_returns_neutral(self) -> None:
        """全馬同値なら全頭 50.0"""
        assert blend_v27([0.4, 0.4, 0.4], [0.6, 0.6, 0.6]) == [50.0, 50.0, 50.0]

    def test_centered_at_50(self) -> None:
        """レース内平均は 50 付近（クリップされない限り）"""
        out = blend_v27([0.2, 0.35, 0.5, 0.65, 0.8], None)
        assert sum(out) / len(out) == pytest.approx(50.0, abs=0.5)

    def test_out_weight_effect_is_monotonic(self) -> None:
        """out_weight を上げるほど高着外率馬の相対順位が下がる"""
        reg = [0.30, 0.32, 0.34]
        out = [0.90, 0.50, 0.40]
        weak = blend_v27(reg, out, out_weight=0.0)
        strong = blend_v27(reg, out, out_weight=2.0)
        # weak では reg 最良の 0 番が最上位、strong では着外率で押し下げられて最下位
        assert weak.index(max(weak)) == 0
        assert strong.index(min(strong)) == 0

    def test_default_weight(self) -> None:
        assert V27_OUT_WEIGHT == 0.5


class TestVersion:
    def test_composite_version(self) -> None:
        assert COMPOSITE_VERSION == 27


@pytest.mark.skipif(
    not _REG_RANK_MODEL_PATH.exists(),
    reason="models/jra_reg_rank_lgb.txt が未生成（scripts/train_jra_reg_rank.py で作成）",
)
class TestRegRankModel:
    def test_model_loads_with_expected_features(self) -> None:
        m = _load_reg_rank_model()
        assert m is not None
        assert m.num_feature() == len(OUT_PROB_FEATURE_NAMES)

    def test_stronger_horse_gets_lower_predicted_rank(self) -> None:
        """サブ指数が高い馬ほど予測正規化着順が小さい（＝上位）"""
        m = _load_reg_rank_model()
        assert m is not None
        n = len(OUT_PROB_FEATURE_NAMES)
        strong, weak = np.full((1, n), 50.0), np.full((1, n), 50.0)
        strong[0, :17] = 75.0
        weak[0, :17] = 25.0
        for row in (strong, weak):
            row[0, 17] = 1600.0  # distance
            row[0, 18] = 16.0    # head_count
        assert float(m.predict(strong)[0]) < float(m.predict(weak)[0])
