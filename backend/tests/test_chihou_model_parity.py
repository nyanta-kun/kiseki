"""地方競馬 本番モデルと配信側の特徴量整合を固定する。

## なぜ必要か

本番は `calculate_and_save(race_id, odds_map=None)` で算出され、`_fetch_win_odds` は
`race_results.win_odds`（レース確定後にしか入らない）を読む。そのため v13 までは
**発走前に市場5特徴が常に中立値**のまま「市場込みで学習したモデル」に食わせていた。

walk-forward 実測（全9四半期・指数1位馬の勝率）:

    市場込み学習・市場なし配信（v13） 23.7〜32.6%
    市場なし学習・市場なし配信（v14） 34.0〜40.1%   ← +6.6〜+13.6pt

v14 で市場5特徴を削除して解消したが、**この種のずれは実行時に例外を出さない**
（LightGBM は特徴量数さえ合えば動く）。列の意味がずれても静かに劣化するだけなので、
モデルファイル・配信側の列定義・学習側の列定義の3者を突き合わせて固定する。
"""

from __future__ import annotations

from pathlib import Path

import lightgbm as lgb
import pytest

from src.indices.chihou_calculator import (
    _LGB_FEATURE_NAMES,
    _PROD_LGB_PATH,
    _PROD_LGB_WIN_PATH,
    CHIHOU_COMPOSITE_VERSION,
)

_MARKET_FEATURES = frozenset({
    "odds_rank_n", "speed_mkt_gap", "kc_mkt_gap", "is_heavy_fav", "is_dark_horse",
})


class TestFeatureList:
    def test_特徴量は39本(self) -> None:
        assert len(_LGB_FEATURE_NAMES) == 39

    def test_重複が無い(self) -> None:
        assert len(set(_LGB_FEATURE_NAMES)) == len(_LGB_FEATURE_NAMES)

    def test_市場特徴が含まれない(self) -> None:
        """配信時に常に中立値になる列をモデルに食わせない。

        戻すなら「配信時に必ずオッズを渡す」経路とセットにすること。
        ただし穴馬用途では市場を見せてはいけない（見せると人気薄を上位に置かず
        「人気薄×指数上位」の条件が空になる）。
        """
        leaked = _MARKET_FEATURES & set(_LGB_FEATURE_NAMES)
        assert not leaked, f"市場特徴が配信側に残っている: {sorted(leaked)}"


class TestModelFiles:
    @pytest.mark.parametrize("path", [_PROD_LGB_PATH, _PROD_LGB_WIN_PATH])
    def test_モデルファイルが存在する(self, path: Path) -> None:
        assert path.exists(), f"本番モデルが無い: {path}"

    @pytest.mark.parametrize("path", [_PROD_LGB_PATH, _PROD_LGB_WIN_PATH])
    def test_モデルの特徴量が配信側と完全一致する(self, path: Path) -> None:
        """順序まで一致していること。

        LightGBM は列数さえ合えば動くので、順序がずれても例外は出ず
        「別の特徴として学習された重み」に値を入れてしまう。
        """
        if not path.exists():
            pytest.skip(f"model not found: {path}")
        booster = lgb.Booster(model_file=str(path))
        assert booster.num_feature() == len(_LGB_FEATURE_NAMES)
        assert booster.feature_name() == list(_LGB_FEATURE_NAMES)


class TestVersion:
    def test_版が上がっている(self) -> None:
        """v14 = 市場特徴を落とした版。下げると DB の履歴と混ざる。"""
        assert CHIHOU_COMPOSITE_VERSION >= 14

    def test_モデルファイル名と版が対応している(self) -> None:
        assert "v14" in _PROD_LGB_PATH.name
        assert "v14" in _PROD_LGB_WIN_PATH.name
