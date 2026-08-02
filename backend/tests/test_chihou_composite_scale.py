"""地方 composite スケール（v13: min-max 廃止）ユニットテスト

背景（2026-08-02）:
  旧実装はレース内 min-max → 15〜85 固定だったため、全レースで幅が 70.00（sd=0）に
  なり、`calculate_race_confidence` の分散スコア(25点)が 100% のレースで満点＝定数化、
  指数差スコア(40点)も 63% が満点となって、**97% のレースが tier S** に張り付いていた。
  v13 で中心化線形スケール（50 + CHIHOU_INDEX_SCALE * (p − レース内平均)）へ変更し、
  tier の較正は confidence 側の gap/dispersion 閾値で吸収する。
"""

from __future__ import annotations

import statistics

from src.indices.chihou_calculator import CHIHOU_INDEX_SCALE, _scale_to_index_local
from src.indices.confidence import (
    CHIHOU_DISPERSION_FULL_SCORE,
    CHIHOU_GAP_FULL_SCORE,
    DEFAULT_DISPERSION_FULL_SCORE,
    calculate_race_confidence,
)


class TestScaleToIndexLocal:
    def test_順位は入力スコアの順序を保つ(self):
        """min-max も中心化線形も単調変換。改修で1位の顔ぶれは変わってはならない。"""
        scores = [0.12, 0.55, 0.31, 0.08, 0.44]
        out = _scale_to_index_local(scores)
        assert sorted(range(len(out)), key=lambda i: out[i]) == \
            sorted(range(len(scores)), key=lambda i: scores[i])

    def test_平均は50になる(self):
        scores = [0.1, 0.2, 0.3, 0.4]
        out = _scale_to_index_local(scores)
        assert statistics.fmean(out) == 50.0

    def test_レースごとに幅が変わる(self):
        """旧 min-max の致命的な問題（全レース幅70固定）が解消されていること。"""
        tight = _scale_to_index_local([0.30, 0.31, 0.32, 0.33])
        loose = _scale_to_index_local([0.05, 0.25, 0.45, 0.75])
        assert (max(tight) - min(tight)) < (max(loose) - min(loose))
        # 旧実装ではどちらも 70.0 になっていた
        assert (max(tight) - min(tight)) != 70.0

    def test_スケール係数が反映される(self):
        scores = [0.2, 0.4]
        out = _scale_to_index_local(scores)
        # 平均0.3 なので 50 ± 0.1*CHIHOU_INDEX_SCALE
        assert out[0] == 50.0 - 0.1 * CHIHOU_INDEX_SCALE
        assert out[1] == 50.0 + 0.1 * CHIHOU_INDEX_SCALE

    def test_0_100にクリップされる(self):
        out = _scale_to_index_local([0.0, 10.0])
        assert min(out) >= 0.0
        assert max(out) <= 100.0

    def test_単一馬は50(self):
        assert _scale_to_index_local([0.42]) == [50.0]

    def test_全馬同値は50(self):
        assert _scale_to_index_local([0.3, 0.3, 0.3]) == [50.0, 50.0, 50.0]


class TestDispersionFullScore:
    def test_既定値はJRA互換(self):
        """JRA の挙動を変えないため、既定値は従来のハードコード値 8.0 のまま。"""
        assert DEFAULT_DISPERSION_FULL_SCORE == 8.0

    def test_閾値を上げると分散スコアが下がる(self):
        """同じ指数分布でも dispersion_full_score が大きいほど満点になりにくい。"""
        idx = [62.0, 56.0, 50.0, 44.0, 38.0]
        strict = calculate_race_confidence(idx, 5, None,
                                           dispersion_full_score=32.0)["score"]
        loose = calculate_race_confidence(idx, 5, None,
                                          dispersion_full_score=4.0)["score"]
        assert strict < loose

    def test_地方の較正値は表示スケールに比例している(self):
        """C=40/gap12/disp16 は C=20/gap6/disp8 と数学的に同値であること。

        表示スケール（CHIHOU_INDEX_SCALE）と tier 較正を分離した設計の要。
        片方だけ変えると tier 分布が壊れる。
        """
        ratio = CHIHOU_INDEX_SCALE / 20.0
        assert CHIHOU_GAP_FULL_SCORE == 6.0 * ratio
        assert CHIHOU_DISPERSION_FULL_SCORE == 8.0 * ratio

    def test_地方較正でスケール倍率が相殺される(self):
        base = [0.40, 0.34, 0.30, 0.26, 0.20]
        mean = statistics.fmean(base)
        small = [50.0 + 20.0 * (p - mean) for p in base]
        large = [50.0 + CHIHOU_INDEX_SCALE * (p - mean) for p in base]
        a = calculate_race_confidence(small, 5, None,
                                      gap_full_score=6.0,
                                      dispersion_full_score=8.0)
        b = calculate_race_confidence(large, 5, None,
                                      gap_full_score=CHIHOU_GAP_FULL_SCORE,
                                      dispersion_full_score=CHIHOU_DISPERSION_FULL_SCORE)
        assert a["score"] == b["score"]
        assert a["rank"] == b["rank"]


class TestTierNoLongerSaturates:
    def test_混戦レースはSにならない(self):
        """旧 min-max では拮抗レースでも幅70になり tier S に張り付いていた。"""
        tight = _scale_to_index_local([0.300, 0.298, 0.296, 0.294, 0.292, 0.290])
        conf = calculate_race_confidence(
            tight, 6, None,
            gap_full_score=CHIHOU_GAP_FULL_SCORE,
            dispersion_full_score=CHIHOU_DISPERSION_FULL_SCORE,
        )
        assert conf["rank"] != "S"

    def test_突出レースは混戦レースより高スコア(self):
        tight = _scale_to_index_local([0.30, 0.29, 0.28, 0.27, 0.26, 0.25])
        clear = _scale_to_index_local([0.75, 0.30, 0.22, 0.18, 0.12, 0.08])
        kw = dict(gap_full_score=CHIHOU_GAP_FULL_SCORE,
                  dispersion_full_score=CHIHOU_DISPERSION_FULL_SCORE)
        assert (calculate_race_confidence(clear, 6, None, **kw)["score"]
                > calculate_race_confidence(tight, 6, None, **kw)["score"])
