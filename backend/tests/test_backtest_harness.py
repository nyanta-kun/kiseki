"""統一バックテストハーネスのユニットテスト。

テスト内容:
  - normalize_combination(): 各券種の表記正規化
  - settle(): 的中/不的中/同着/返還の決済正誤
  - _bootstrap_roi_ci(): CI 計算の基本動作
  - SettleResult 集計の正確性

DB アクセスはモック（MagicMock）で実施。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from src.betting.backtest import (
    Bet,
    _bootstrap_roi_ci,
    normalize_combination,
    settle,
)

# ---------------------------------------------------------------------------
# normalize_combination テスト
# ---------------------------------------------------------------------------


class TestNormalizeCombination:
    """normalize_combination() の正規化ルール検証。"""

    def test_win_single_horse(self) -> None:
        """単勝: 馬番1つを文字列化する。"""
        assert normalize_combination("win", [13]) == "13"
        assert normalize_combination("win", [1]) == "1"

    def test_place_single_horse(self) -> None:
        """複勝: 馬番1つを文字列化する。"""
        assert normalize_combination("place", [7]) == "7"

    def test_quinella_sorted(self) -> None:
        """馬連: 昇順ソートして "-" 結合。"""
        assert normalize_combination("quinella", [13, 1]) == "1-13"
        assert normalize_combination("quinella", [1, 13]) == "1-13"
        assert normalize_combination("quinella", [2, 4]) == "2-4"

    def test_wide_sorted(self) -> None:
        """ワイド: 昇順ソートして "-" 結合。"""
        assert normalize_combination("wide", [16, 1]) == "1-16"
        assert normalize_combination("wide", [4, 7]) == "4-7"

    def test_trio_sorted(self) -> None:
        """三連複: 昇順ソートして "-" 結合。"""
        assert normalize_combination("trio", [16, 1, 13]) == "1-13-16"
        assert normalize_combination("trio", [4, 11, 12]) == "4-11-12"
        assert normalize_combination("trio", [1, 2, 3]) == "1-2-3"

    def test_exacta_order_preserved(self) -> None:
        """馬単: 着順どおり（ソートしない）。"""
        assert normalize_combination("exacta", [13, 16]) == "13-16"
        assert normalize_combination("exacta", [16, 13]) == "16-13"

    def test_trifecta_order_preserved(self) -> None:
        """三連単: 着順どおり（ソートしない）。"""
        assert normalize_combination("trifecta", [13, 16, 1]) == "13-16-1"
        assert normalize_combination("trifecta", [4, 2, 5]) == "4-2-5"

    def test_bracket_sorted(self) -> None:
        """枠連: 昇順ソートして連結（区切りなし）。"""
        assert normalize_combination("bracket", [8, 7]) == "78"
        assert normalize_combination("bracket", [2, 4]) == "24"
        assert normalize_combination("bracket", [8, 8]) == "88"

    def test_invalid_bet_type(self) -> None:
        """無効な bet_type は ValueError を raise する。"""
        with pytest.raises(ValueError, match="未対応 bet_type"):
            normalize_combination("invalid", [1])

    def test_win_too_many_horses(self) -> None:
        """単勝に馬番2つは ValueError。"""
        with pytest.raises(ValueError):
            normalize_combination("win", [1, 2])

    def test_quinella_wrong_count(self) -> None:
        """馬連に馬番1つは ValueError。"""
        with pytest.raises(ValueError):
            normalize_combination("quinella", [1])

    def test_trio_wrong_count(self) -> None:
        """三連複に馬番2つは ValueError。"""
        with pytest.raises(ValueError):
            normalize_combination("trio", [1, 2])


# ---------------------------------------------------------------------------
# Bet dataclass テスト
# ---------------------------------------------------------------------------


class TestBet:
    """Bet dataclass の検証。"""

    def test_valid_bet(self) -> None:
        """正常な Bet が作成できる。"""
        bet = Bet(race_id=1, bet_type="win", combination="13", stake=100, tag="test")
        assert bet.race_id == 1
        assert bet.bet_type == "win"
        assert bet.combination == "13"
        assert bet.stake == 100
        assert bet.tag == "test"

    def test_invalid_bet_type(self) -> None:
        """無効な bet_type は ValueError。"""
        with pytest.raises(ValueError, match="未対応 bet_type"):
            Bet(race_id=1, bet_type="unknown", combination="1", stake=100)

    def test_invalid_stake_not_multiple_of_100(self) -> None:
        """stake が 100 の倍数でない場合は ValueError。"""
        with pytest.raises(ValueError, match="stake"):
            Bet(race_id=1, bet_type="win", combination="1", stake=150)

    def test_invalid_stake_zero(self) -> None:
        """stake=0 は ValueError。"""
        with pytest.raises(ValueError, match="stake"):
            Bet(race_id=1, bet_type="win", combination="1", stake=0)

    def test_default_stake(self) -> None:
        """stake のデフォルト値は 100。"""
        bet = Bet(race_id=1, bet_type="win", combination="1")
        assert bet.stake == 100

    def test_default_tag(self) -> None:
        """tag のデフォルト値は空文字列。"""
        bet = Bet(race_id=1, bet_type="win", combination="1")
        assert bet.tag == ""


# ---------------------------------------------------------------------------
# settle() モックテスト
# ---------------------------------------------------------------------------


def _make_mock_conn(payout_rows: list[tuple], date_rows: list[tuple]) -> MagicMock:
    """settle() が使う conn.execute() をモック化する。

    Args:
        payout_rows: (race_id, bet_type, combination, payout) の行リスト
        date_rows:   (race_id, date) の行リスト
    """
    mock_conn = MagicMock()

    # execute() の呼び出しを2回分設定
    # 1回目: race_payouts クエリ
    # 2回目: races date クエリ
    mock_payouts_result = MagicMock()
    mock_payouts_result.__iter__ = MagicMock(return_value=iter(payout_rows))

    mock_dates_result = MagicMock()
    mock_dates_result.__iter__ = MagicMock(return_value=iter(date_rows))

    mock_conn.execute.side_effect = [mock_payouts_result, mock_dates_result]
    return mock_conn


class TestSettle:
    """settle() の決済ロジック検証。"""

    def test_win_hit(self) -> None:
        """単勝: 的中ケース。"""
        bets = [Bet(race_id=1, bet_type="win", combination="13", stake=100, tag="t")]
        payout_rows = [(1, "win", "13", 160)]  # 1.6倍
        date_rows = [(1, "2025-07-01")]

        conn = _make_mock_conn(payout_rows, date_rows)
        result = settle(bets, conn, n_bootstrap=100)

        assert len(result.bet_results) == 1
        br = result.bet_results[0]
        assert br.is_hit is True
        assert br.payout == 160  # 100円賭け × 1.6倍
        assert br.is_refund is False

    def test_win_miss(self) -> None:
        """単勝: 不的中ケース。"""
        bets = [Bet(race_id=1, bet_type="win", combination="5", stake=100, tag="t")]
        payout_rows = [(1, "win", "13", 160)]  # 13番が的中
        date_rows = [(1, "2025-07-01")]

        conn = _make_mock_conn(payout_rows, date_rows)
        result = settle(bets, conn, n_bootstrap=100)

        br = result.bet_results[0]
        assert br.is_hit is False
        assert br.payout == 0

    def test_trio_hit(self) -> None:
        """三連複: 的中ケース（組み合わせが昇順ソート済み）。"""
        bets = [Bet(race_id=1, bet_type="trio", combination="1-13-16", stake=100, tag="t")]
        payout_rows = [(1, "trio", "1-13-16", 54450)]
        date_rows = [(1, "2025-07-01")]

        conn = _make_mock_conn(payout_rows, date_rows)
        result = settle(bets, conn, n_bootstrap=100)

        br = result.bet_results[0]
        assert br.is_hit is True
        assert br.payout == 54450

    def test_trifecta_hit(self) -> None:
        """三連単: 的中ケース（着順保持）。"""
        bets = [Bet(race_id=1, bet_type="trifecta", combination="13-16-1", stake=100, tag="t")]
        payout_rows = [(1, "trifecta", "13-16-1", 133720)]
        date_rows = [(1, "2025-07-01")]

        conn = _make_mock_conn(payout_rows, date_rows)
        result = settle(bets, conn, n_bootstrap=100)

        br = result.bet_results[0]
        assert br.is_hit is True
        assert br.payout == 133720

    def test_doubling_stake(self) -> None:
        """stake=200 の場合は払戻も2倍になる。"""
        bets = [Bet(race_id=1, bet_type="win", combination="13", stake=200, tag="t")]
        payout_rows = [(1, "win", "13", 160)]
        date_rows = [(1, "2025-07-01")]

        conn = _make_mock_conn(payout_rows, date_rows)
        result = settle(bets, conn, n_bootstrap=100)

        br = result.bet_results[0]
        assert br.payout == 320  # 160 × (200/100)

    def test_dead_heat_trio_max_payout(self) -> None:
        """同着（三連複に複数払戻行）: 最大払戻を採用する。"""
        bets = [Bet(race_id=531, bet_type="trifecta", combination="2-4-8", stake=100, tag="t")]
        # 同着で "2-4-8"=4390 と "2-4-9"=440 の2行
        payout_rows = [
            (531, "trifecta", "2-4-8", 4390),
            (531, "trifecta", "2-4-9", 440),
        ]
        date_rows = [(531, "2025-07-01")]

        conn = _make_mock_conn(payout_rows, date_rows)
        result = settle(bets, conn, n_bootstrap=100)

        br = result.bet_results[0]
        assert br.is_hit is True
        assert br.payout == 4390  # 最大払戻

    def test_place_refund_payout100(self) -> None:
        """元返し（payout=100）は的中として処理される（払戻あり）。"""
        bets = [Bet(race_id=1, bet_type="place", combination="4", stake=100, tag="t")]
        payout_rows = [(1, "place", "4", 100)]  # 元返し
        date_rows = [(1, "2025-07-01")]

        conn = _make_mock_conn(payout_rows, date_rows)
        result = settle(bets, conn, n_bootstrap=100)

        br = result.bet_results[0]
        assert br.is_hit is True
        assert br.payout == 100

    def test_tag_summary_roi(self) -> None:
        """タグサマリーの ROI 計算が正しい。"""
        # 2件ベット: 1件的中 (160円)、1件不的中 (0円)
        bets = [
            Bet(race_id=1, bet_type="win", combination="13", stake=100, tag="t"),
            Bet(race_id=2, bet_type="win", combination="5", stake=100, tag="t"),
        ]
        payout_rows = [
            (1, "win", "13", 160),
            (2, "win", "1", 100),  # 1番が的中、5番は不的中
        ]
        date_rows = [(1, "2025-07-01"), (2, "2025-07-02")]

        # 2回 execute が呼ばれる（payout + dates）
        mock_conn = MagicMock()
        mock_payouts = MagicMock()
        mock_payouts.__iter__ = MagicMock(return_value=iter(payout_rows))
        mock_dates = MagicMock()
        mock_dates.__iter__ = MagicMock(return_value=iter(date_rows))
        mock_conn.execute.side_effect = [mock_payouts, mock_dates]

        result = settle(bets, mock_conn, n_bootstrap=100)

        ts = result.tag_summaries[0]
        assert ts.tag == "t"
        assert ts.bet_type == "win"
        assert ts.n_bets == 2
        assert ts.n_hits == 1
        assert ts.total_stake == 200
        assert ts.total_payout == 160
        assert abs(ts.roi - 0.8) < 1e-9

    def test_period_summary(self) -> None:
        """期間別サマリーが正しく分割される。"""
        bets = [
            Bet(race_id=1, bet_type="win", combination="1", stake=100, tag="t"),
            Bet(race_id=2, bet_type="win", combination="2", stake=100, tag="t"),
        ]
        payout_rows = [
            (1, "win", "1", 200),
            (2, "win", "3", 150),
        ]
        date_rows = [(1, "2025-06-15"), (2, "2025-08-01")]

        mock_conn = MagicMock()
        mock_payouts = MagicMock()
        mock_payouts.__iter__ = MagicMock(return_value=iter(payout_rows))
        mock_dates = MagicMock()
        mock_dates.__iter__ = MagicMock(return_value=iter(date_rows))
        mock_conn.execute.side_effect = [mock_payouts, mock_dates]

        period_splits = [
            ("train", "20230101", "20250630"),
            ("test", "20250701", "20260331"),
        ]
        result = settle(bets, mock_conn, period_splits=period_splits, n_bootstrap=100)

        assert len(result.period_summaries) == 2
        # race_id=1 は train 期間（2025-06-15 → "20250615" ∈ train）
        # race_id=2 は test 期間（2025-08-01 → "20250801" ∈ test）
        train_ps = next(p for p in result.period_summaries if p.period_label == "train")
        test_ps = next(p for p in result.period_summaries if p.period_label == "test")
        assert train_ps.n_bets == 1
        assert test_ps.n_bets == 1
        assert train_ps.n_hits == 1  # race_id=1 は "1" が的中
        assert test_ps.n_hits == 0  # race_id=2 は "2" で購入→ "3" が的中→不的中

    def test_monthly_rows(self) -> None:
        """月次推移が正しく集計される。"""
        bets = [
            Bet(race_id=1, bet_type="win", combination="1", stake=100, tag="t"),
            Bet(race_id=2, bet_type="win", combination="2", stake=100, tag="t"),
        ]
        payout_rows = [
            (1, "win", "1", 200),
            (2, "win", "2", 300),
        ]
        date_rows = [(1, "2025-07-10"), (2, "2025-08-01")]

        mock_conn = MagicMock()
        mock_payouts = MagicMock()
        mock_payouts.__iter__ = MagicMock(return_value=iter(payout_rows))
        mock_dates = MagicMock()
        mock_dates.__iter__ = MagicMock(return_value=iter(date_rows))
        mock_conn.execute.side_effect = [mock_payouts, mock_dates]

        result = settle(bets, mock_conn, n_bootstrap=100)

        # 2ヶ月分
        assert len(result.monthly_rows) == 2
        july = result.monthly_rows[0]
        aug = result.monthly_rows[1]
        assert july.ym == "202507"
        assert aug.ym == "202508"
        assert july.n_hits == 1
        assert aug.n_hits == 1
        # 累積損益: 7月 +100、8月 +200 → 累積 +300
        assert july.cumulative_profit == 100
        assert aug.cumulative_profit == 300

    def test_empty_bets(self) -> None:
        """ベットが空の場合は空のサマリーを返す。"""
        mock_conn = MagicMock()
        result = settle([], mock_conn)
        assert result.bet_results == []
        assert result.tag_summaries == []


# ---------------------------------------------------------------------------
# ブートストラップ CI テスト
# ---------------------------------------------------------------------------


class TestBootstrapRoiCi:
    """_bootstrap_roi_ci() の動作検証。"""

    def test_ci_contains_true_roi(self) -> None:
        """CI が真のROIを内包することを確認（確率論的だが高確率）。"""
        rng = np.random.default_rng(42)
        # ROI ≈ 0.8 の合成データ
        stakes = np.ones(200) * 100
        payouts = np.where(rng.random(200) < 0.5, 160.0, 0.0)
        true_roi = payouts.sum() / stakes.sum()

        lower, upper = _bootstrap_roi_ci(stakes, payouts, n_iter=5000, seed=42)

        assert not np.isnan(lower)
        assert not np.isnan(upper)
        assert lower < upper
        # 95% CI なので true_roi が CI 内に収まる（広いので必ず）
        assert lower <= true_roi <= upper

    def test_ci_empty(self) -> None:
        """空配列は (nan, nan) を返す。"""
        lower, upper = _bootstrap_roi_ci(np.array([]), np.array([]))
        assert np.isnan(lower)
        assert np.isnan(upper)

    def test_ci_width_increases_with_variance(self) -> None:
        """分散が大きいとCI幅が広がる。"""
        stakes = np.ones(100) * 100
        # 低分散: 全て均等払戻
        payouts_low = np.ones(100) * 80
        # 高分散: 大当たり or ゼロ
        payouts_high = np.where(np.arange(100) % 10 == 0, 1000.0, 0.0)

        lower_l, upper_l = _bootstrap_roi_ci(stakes, payouts_low, n_iter=3000, seed=42)
        lower_h, upper_h = _bootstrap_roi_ci(stakes, payouts_high, n_iter=3000, seed=42)

        width_low = upper_l - lower_l
        width_high = upper_h - lower_h
        assert width_high > width_low


# ---------------------------------------------------------------------------
# 正規化 ↔ DB 表記の一貫性
# ---------------------------------------------------------------------------


class TestCombinationConsistency:
    """normalize_combination() が race_payouts の表記と一致することを確認。"""

    def test_known_race_combinations(self) -> None:
        """DB 監査で確認した実際の組合せが normalize_combination() で生成できる。

        race_id=35859 の実データ:
          win: "13"  → horse 13 が1着
          trio: "1-13-16" → 昇順ソート
          trifecta: "13-16-1" → 着順 1st=13, 2nd=16, 3rd=1
          quinella: "13-16" → 昇順ソート
          wide: "1-13", "1-16", "13-16" → 昇順ソート
        """
        assert normalize_combination("win", [13]) == "13"
        assert normalize_combination("trio", [16, 1, 13]) == "1-13-16"
        assert normalize_combination("trifecta", [13, 16, 1]) == "13-16-1"
        assert normalize_combination("quinella", [16, 13]) == "13-16"
        assert normalize_combination("wide", [13, 1]) == "1-13"
        assert normalize_combination("wide", [16, 1]) == "1-16"
        assert normalize_combination("wide", [16, 13]) == "13-16"

    def test_bracket_known_format(self) -> None:
        """枠連: 実DBで確認した表記 "78" → 枠7と枠8。"""
        assert normalize_combination("bracket", [7, 8]) == "78"
        assert normalize_combination("bracket", [8, 7]) == "78"  # 逆順も同じ結果

    def test_dead_heat_race531(self) -> None:
        """同着レース531: trio に複数の組み合わせが存在する。

        実データ:
          trio: "2-4-8"=1170  (3着が8番の場合)
          trio: "2-4-9"=130   (3着が9番の場合、同着)
        """
        assert normalize_combination("trio", [2, 4, 8]) == "2-4-8"
        assert normalize_combination("trio", [2, 4, 9]) == "2-4-9"
