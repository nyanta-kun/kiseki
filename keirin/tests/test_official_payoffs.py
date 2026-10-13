"""採点の払戻が**公式払戻**から作られることの回帰テスト（2026-10-13 新設）。

## なぜ要るか

採点は「確定オッズ × 賭け金」で払戻を作っていたが、**同着では公式払戻が
オッズの約半分**になる（売上を当たり目の間で分けて払うため）。

    20261013_81_05 小倉5R（3着 2番・3番 同着）
      三連単 1-5-2  オッズ 52.1倍  → 公式払戻 2,620円
      三連単 1-5-3  オッズ 118.9倍 → 公式払戻 5,890円
      L_lead 2,500円×2点   採点 427,500円 ↔ 正 212,750円

例外もログも出ず、回収率が静かに上振れする壊れ方なので、
実際の採点経路（`settle_type_lab_picks.main`）を SQLite で通して固定する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from src import official_payoffs as OP  # noqa: E402
from src.database import get_connection  # noqa: E402
from src.scraper.winticket import parse_payoffs  # noqa: E402

import settle_type_lab_picks as S  # noqa: E402

#: 本物と衝突しない日付（テストは共有の SQLite を使う）
RK = "29991013_81_05"
DAY = "2999-10-13"


def _item(key, odds, pay):
    return {"key": key, "odds": odds, "payoffUnitPrice": pay, "absent": False}


#: winticket `FETCH_KEIRIN_RACE_ODDS` の小倉5R（必要な目だけ抜粋・値は実物）
KOKURA5 = {
    "trifecta": [_item([1, 2, 3], 254.1, 0), _item([1, 5, 2], 52.1, 2620),
                 _item([1, 5, 3], 118.9, 5890)],
    "trio": [_item([1, 2, 5], 15.7, 800), _item([1, 3, 5], 39.4, 1910),
             _item([2, 5, 7], 9.4, 0)],
    "trifectaPayoffStatus": 1,
    "trioPayoffStatus": 1,
}


# ───────────────── 取り込み側 ─────────────────

def test_parse_reads_payoff_not_odds():
    """🔴 `odds` ではなく `payoffUnitPrice` を読む。外れ目（0）は返さない。"""
    got = parse_payoffs(KOKURA5)
    assert got["confirmed"] == {"trifecta": True, "trio": True}
    assert sorted((i["bet_type"], i["combination"], i["payoff"]) for i in got["items"]) == [
        ("trifecta", "1-5-2", 2620), ("trifecta", "1-5-3", 5890),
        ("trio", "1-2-5", 800), ("trio", "1-3-5", 1910),
    ]


def test_parse_ignores_unconfirmed_markets():
    """確定前（`PayoffStatus=0`）の値は取り込まない。結果直後は 0 のことがある。"""
    data = dict(KOKURA5, trifectaPayoffStatus=0)
    got = parse_payoffs(data)
    assert got["confirmed"]["trifecta"] is False
    assert {i["bet_type"] for i in got["items"]} == {"trio"}


def test_trio_key_is_sorted_and_matches_wt_odds_notation():
    assert OP.key("trio", "5=1=2") == "1-2-5"
    assert OP.key("trifecta", "1-5-2") == "1-5-2"
    assert OP.key("trio", [3, 1, 5]) == "1-3-5"


# ───────────────── 採点側（実経路） ─────────────────

LEGS = [{"combo": "1-5-2", "stake": 2500}, {"combo": "1-5-3", "stake": 2500},
        {"combo": "1-5-4", "stake": 2500}, {"combo": "1-5-7", "stake": 2500}]


@pytest.fixture
def kokura_race():
    """小倉5R を SQLite に置く（着順・オッズ・選手。公式払戻は各テストで入れる）。"""
    with get_connection() as c:
        for t in ("type_lab_picks", "wt_entries", "wt_odds", "wt_payoffs", "wt_races"):
            c.execute(f"DELETE FROM {t} WHERE race_key = ?", (RK,))
        for fo, fn in [(1, 1), (2, 5), (3, 2), (3, 3), (5, 7), (6, 6), (7, 4)]:
            c.execute("INSERT INTO wt_entries (race_key, frame_no, finish_order) VALUES (?, ?, ?)",
                      (RK, fn, fo))
        for comb, od in [("1-5-2", 52.1), ("1-5-3", 118.9), ("1-5-4", 41.5), ("1-5-7", 170.4)]:
            c.execute("INSERT INTO wt_odds (race_key, bet_type, combination, odds_value) "
                      "VALUES (?, 'trifecta', ?, ?)", (RK, comb, od))
        c.execute(
            "INSERT INTO type_lab_picks (race_key, race_date, type_label, mode, plan_key, "
            "bet_type, n_legs, budget, legs, rule_version) "
            "VALUES (?, ?, 'L', 'live', 'L_lead', 'trifecta', 4, 10000, ?, 'test')",
            (RK, DAY, json.dumps(LEGS)))
        c.commit()
    yield
    with get_connection() as c:
        for t in ("type_lab_picks", "wt_entries", "wt_odds", "wt_payoffs", "wt_races"):
            c.execute(f"DELETE FROM {t} WHERE race_key = ?", (RK,))
        c.commit()


def _settle(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["settle", "--date", DAY, "--no-fetch"])
    S.main()
    with get_connection() as c:
        return c.execute("SELECT settled_at, hit, payout, final_odds FROM type_lab_picks "
                         "WHERE race_key = ?", (RK,)).fetchone()


def test_dead_heat_is_paid_at_the_official_payoff(kokura_race, monkeypatch):
    """🔴 同着の両目を買っていれば両方とも、**公式払戻で**払う。

    オッズで作ると 2,500×52.1 + 2,500×118.9 = 427,500円（2倍に化ける）。
    """
    with get_connection() as c:
        c.executemany("INSERT INTO wt_payoffs (race_key, bet_type, combination, payoff) "
                      "VALUES (?, 'trifecta', ?, ?)", [(RK, "1-5-2", 2620), (RK, "1-5-3", 5890)])
        c.commit()
    settled_at, hit, payout, final_odds = _settle(monkeypatch)
    assert settled_at is not None and hit
    assert payout == 2500 * 2620 // 100 + 2500 * 5890 // 100 == 212_750
    assert final_odds == pytest.approx(26.2)


def test_no_official_payoff_means_wait_not_odds(kokura_race, monkeypatch):
    """🔴 公式払戻が無ければ**採点しない**。オッズで代用すると同着で2倍に戻る。"""
    settled_at, _, payout, _ = _settle(monkeypatch)
    assert settled_at is None and payout is None


def test_settlement_does_not_price_wins_from_the_odds_board():
    """経路の固定: 払戻の計算式が `wt_odds` のオッズを掛けていないこと。"""
    src = (REPO / "scripts" / "settle_type_lab_picks.py").read_text(encoding="utf-8")
    assert "official_payoffs.collect" in src and "official_payoffs.load" in src
    assert 'l["stake"]\n' not in src and "* odds[" not in src
