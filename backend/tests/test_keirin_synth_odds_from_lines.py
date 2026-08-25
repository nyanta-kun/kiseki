"""売った商品の合成オッズは**入稿データのオッズだけ**から出す（2026-08-26）。

🔴 旧実装は `wt_odds_snapshot`（板）を引いていた。朝の板は薄く、5点のうち
   板にあるのが2点だけという状態が普通に起きる。合成オッズは点が少ないほど
   大きくなるので、**一部しか照合できなかったレースほど大きく表示されていた**。

入稿側は 2026-08-26 から三連複も三連単も予測オッズしか書かない
（`keirin/scripts/netkeirin_submit_wt.py`）。表示もそこから出せば数字が揃う。
"""
from __future__ import annotations

from src.api.keirin_router import _calc_synth_odds_from_lines


def test_合成オッズは各点の逆数和の逆数():
    lines = [{"bet_type": "3連複", "combo": "1=2=3", "odds": 4.0},
             {"bet_type": "3連複", "combo": "1=2=4", "odds": 4.0}]
    assert _calc_synth_odds_from_lines(lines) == 2.0


def test_三連単も計算できる():
    lines = [{"bet_type": "3連単", "combo": "1-2-3", "odds": 50.0},
             {"bet_type": "3連単", "combo": "1-2-4", "odds": 50.0}]
    assert _calc_synth_odds_from_lines(lines) == 25.0


def test_1点でも欠けたらNone():
    """🔴 残りだけで合成すると過大表示になる。分からないときは出さない。"""
    lines = [{"bet_type": "3連複", "combo": "1=2=3", "odds": 4.0},
             {"bet_type": "3連複", "combo": "1=2=4", "odds": None}]
    assert _calc_synth_odds_from_lines(lines) is None
    assert _calc_synth_odds_from_lines(
        [{"bet_type": "3連複", "combo": "1=2=3", "odds": 0}]) is None


def test_未知の券種は混ぜない():
    assert _calc_synth_odds_from_lines(
        [{"bet_type": "2連単", "combo": "1-2", "odds": 4.0}]) is None


def test_空ならNone():
    assert _calc_synth_odds_from_lines([]) is None


def test_板を引かない():
    """構造で塞ぐ。DB も引数に取らない＝板を見ようがない。"""
    import inspect
    assert "db" not in inspect.signature(_calc_synth_odds_from_lines).parameters
    assert not inspect.iscoroutinefunction(_calc_synth_odds_from_lines)
