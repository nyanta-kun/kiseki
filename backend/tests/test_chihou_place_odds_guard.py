"""複勝バックテストの母集団ガード検査。

2026-01〜03 の `place_odds` は「1-3着 98.3% / 4着以下 0.0%」という着順相関した
欠損を持つ。バックテストが NULL 行を黙って落としていたため、母集団の的中率が
定義上ほぼ 100% になり複勝 ROI が壊滅的に上振れしていた。
本テストはその形のデータでガードが母集団ごと落とすことを固定する。
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.services.chihou_place_odds_guard import (
    MAX_FINISH_POSITION_SKEW,
    MIN_RACE_PLACE_ODDS_COVERAGE,
    filter_races_with_full_place_odds,
)


def _race(race_id: int, n: int, filled: set[int]) -> pd.DataFrame:
    """着順 1..n のレースを作る。filled に入れた着順だけ place_odds を持つ。"""
    return pd.DataFrame({
        "race_id": [race_id] * n,
        "finish_position": list(range(1, n + 1)),
        "place_odds": [1.5 + i if (i + 1) in filled else None for i in range(n)],
    })


class TestHrOnlyPeriodIsExcluded:
    """HR払戻由来（1〜3着だけ埋まる）のレースは母集団から落ちること。"""

    def test_top3_only_race_is_dropped(self) -> None:
        df = _race(1, 10, filled={1, 2, 3})
        out, audit = filter_races_with_full_place_odds(df)
        assert out.empty
        assert audit.n_races_after == 0
        assert audit.is_skewed_before

    def test_audit_reports_the_skew(self) -> None:
        df = _race(1, 10, filled={1, 2, 3})
        _, audit = filter_races_with_full_place_odds(df)
        assert audit.top3_fill_before == pytest.approx(1.0)
        assert audit.rest_fill_before == pytest.approx(0.0)
        assert audit.skew_before == pytest.approx(1.0)
        assert "⚠️" in audit.format()

    def test_hit_rate_would_have_been_100pct_without_guard(self) -> None:
        """ガードが無いと的中率が定義上 100% になることを明示的に示す。"""
        df = _race(1, 10, filled={1, 2, 3})
        naive = df[df["place_odds"].notna()]
        hits = naive["finish_position"].between(1, 3, inclusive="both").sum()
        assert hits == len(naive)  # 母集団が全部当たりになる

        guarded, _ = filter_races_with_full_place_odds(df)
        assert guarded.empty  # ガードはこの母集団を採用しない


class TestFullyCoveredRaceIsKept:
    """odds_history がある期間（全馬に複勝オッズがある）は残ること。"""

    def test_all_filled_race_is_kept(self) -> None:
        df = _race(1, 10, filled=set(range(1, 11)))
        out, audit = filter_races_with_full_place_odds(df)
        assert len(out) == 10
        assert audit.n_races_after == 1
        assert not audit.is_skewed_before

    def test_one_missing_horse_is_tolerated(self) -> None:
        """取消等で数頭欠けても閾値(90%)以上なら採用する。"""
        df = _race(1, 10, filled=set(range(1, 10)))  # 10頭中9頭
        out, _ = filter_races_with_full_place_odds(df)
        assert len(out) == 10

    def test_below_threshold_race_is_dropped(self) -> None:
        df = _race(1, 10, filled=set(range(1, 9)))  # 80% < 90%
        out, _ = filter_races_with_full_place_odds(df)
        assert out.empty


class TestMixedPeriods:
    """健全なレースと HR のみのレースが混ざった母集団を正しく分離すること。"""

    def test_only_full_races_survive(self) -> None:
        df = pd.concat([
            _race(1, 10, filled=set(range(1, 11))),   # 2026-04以降 相当
            _race(2, 10, filled={1, 2, 3}),           # 2026-01〜03 相当
            _race(3, 12, filled=set(range(1, 13))),   # 2026-04以降 相当
            _race(4, 12, filled=set()),               # 2024〜2025 相当（全欠損）
        ], ignore_index=True)
        out, audit = filter_races_with_full_place_odds(df)
        assert set(out["race_id"].unique()) == {1, 3}
        assert audit.n_races_before == 4
        assert audit.n_races_after == 2


class TestEdgeCases:
    def test_empty_frame(self) -> None:
        df = pd.DataFrame(columns=["race_id", "finish_position", "place_odds"])
        out, audit = filter_races_with_full_place_odds(df)
        assert out.empty
        assert audit.n_races_before == 0

    def test_thresholds_are_meaningful(self) -> None:
        assert 0.5 < MIN_RACE_PLACE_ODDS_COVERAGE <= 1.0
        assert 0.0 < MAX_FINISH_POSITION_SKEW < 1.0
