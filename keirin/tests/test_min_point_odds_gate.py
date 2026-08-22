"""1点でも安すぎる目があるレースを入稿しない規則を固定する（2026-08-22）。

ユーザー方針「**買い目の1点でも予想オッズが 2.0 倍を切っているレースは推奨から
外す**」（掛金の半分を入れて元返しにしかならない目を売らない）。

🔴 この規則は**収支の改善策ではない**。実測では落ちる側のほうが的中率もROIも
   高い。採る理由は「2倍以上での的中」が落ちる側に1件も無いこと＝KPI 上の
   損失がゼロであることと、規則の説明性。根拠の数字は
   `src/stake_allocation.MIN_POINT_ODDS` のコメント。

ここで固定するのは3つ:
  1. 判定そのもの（境界は 2.0 ちょうどを**通す**）
  2. **オッズが1点でも欠けたら判定しない**（＝出す側へ倒す）
  3. 入稿経路がこの判定を通っていること（規則が実装から外れていないこと）
"""
from __future__ import annotations

import re
from pathlib import Path

from src.stake_allocation import MIN_POINT_ODDS, cheap_point_odds

ROOT = Path(__file__).resolve().parent.parent


def test_安い目があればその最低倍率を返す():
    assert cheap_point_odds({1: 3.0, 2: 1.8, 3: 5.0}) == 1.8


def test_境界の2倍ちょうどは通す():
    """「2倍を切っている」なので 2.0 は対象外。"""
    assert cheap_point_odds({1: 2.0, 2: 3.0}) is None
    assert cheap_point_odds({1: 1.99, 2: 3.0}) == 1.99


def test_全部高ければNone():
    assert cheap_point_odds({1: 4.5, 2: 9.0}) is None


def test_オッズが欠けたら判定しない():
    """🔴 欠けた目が最安だった可能性がある。分からないことを理由に落とさない。"""
    assert cheap_point_odds({1: 3.0, 2: None}) is None      # type: ignore[dict-item]
    assert cheap_point_odds({1: 3.0, 2: 0}) is None
    assert cheap_point_odds({}) is None


def test_閾値は引数で変えられる():
    assert cheap_point_odds({1: 2.5}, minimum=3.0) == 2.5
    assert cheap_point_odds({1: 2.5}, minimum=2.0) is None


def test_既定値は2倍():
    assert MIN_POINT_ODDS == 2.0


def test_入稿経路がこの判定を通っている():
    """🔴 規則が実装から外れると、例外もログも出ずに元の挙動へ戻る。"""
    src = (ROOT / "scripts" / "netkeirin_submit_wt.py").read_text(encoding="utf-8")
    assert "cheap_point_odds" in src, "入稿経路が判定を呼んでいない"
    assert re.search(r"_cheap\s*=\s*cheap_point_odds", src), "判定の呼び出し形が変わった"
    assert re.search(r"if _cheap is not None:\s*\n.*?continue", src, re.S), \
        "該当レースを `continue` で飛ばしていない"


def test_想定払戻の下限とは別物であること():
    """⚠️ `expected_payout_floor` は 賭け金×オッズ÷予算。こちらは生のオッズ。

    5点買いなら 2.0倍の目でも払戻は 2,000×2.0 = 4,000円（下限 0.4倍）。
    片方をもう片方の代わりに使えないことを、値で示しておく。
    """
    from src.stake_allocation import expected_payout_floor

    stakes = {1: 2000, 2: 2000, 3: 2000, 4: 2000, 5: 2000}
    odds = {1: 2.0, 2: 5.0, 3: 5.0, 4: 5.0, 5: 5.0}
    assert cheap_point_odds(odds) is None            # 生オッズは 2.0 なので通る
    assert expected_payout_floor(stakes, odds, 10000) == 0.4   # 払戻の下限は 0.4倍
