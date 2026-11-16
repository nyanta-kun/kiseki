"""snapshot_prestart_odds_wt.py（発走直前の t10 / t07）のテスト。

固定すること:
- 窓の境界（10分・7分・発走済み）
- 取り逃した t10 を t07 の値で埋めないこと
- 2回目の起動で増えないこと（INSERT OR IGNORE・最初の値が残る）
- 3連複・3連単だけを保存すること
- 取得中に発走を過ぎた値を捨てること
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from snapshot_prestart_odds_wt import T07, T10, due_type, run  # noqa: E402

_JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=_JST)


@pytest.mark.parametrize("sec, have, expected", [
    (11 * 60, set(), None),          # まだ早い
    (10 * 60, set(), T10),           # 10分ちょうどは t10
    (8 * 60, set(), T10),
    (7 * 60 + 1, set(), T10),
    (7 * 60, set(), T07),            # 7分ちょうどは t07
    (60, set(), T07),
    (0, set(), None),                # 発走時刻
    (-30, set(), None),              # 発走済み
    (8 * 60, {T10}, None),           # 取得済み
    (5 * 60, {T10}, T07),
    (5 * 60, {T07}, None),
    (5 * 60, {T10, T07}, None),
])
def test_due_type(sec, have, expected):
    assert due_type(sec, have) == expected


def test_missed_t10_is_not_backfilled_with_t07_value():
    """10分前を取り逃したレースは、7分以内では t07 だけを返す（t10 にしない）。"""
    assert due_type(6 * 60, set()) == T07


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE wt_races (
            race_key TEXT PRIMARY KEY, venue_id TEXT, race_date TEXT, race_no INTEGER,
            cup_id TEXT, day_index INTEGER, start_at TEXT, cancel INTEGER DEFAULT 0
        );
        CREATE TABLE wt_odds_snapshot (
            id INTEGER PRIMARY KEY, race_key TEXT NOT NULL, bet_type TEXT NOT NULL,
            combination TEXT NOT NULL, odds_value REAL,
            snapshot_type TEXT NOT NULL, snapshot_at TEXT,
            UNIQUE(race_key, bet_type, combination, snapshot_type)
        );
    """)
    return conn


def _race(conn, rk, minutes, cancel=0, date="2026-11-15"):
    start = int((NOW + timedelta(minutes=minutes)).timestamp())
    conn.execute("INSERT INTO wt_races VALUES (?,?,?,?,?,?,?,?)",
                 (rk, "23", date, 1, "c", 1, str(start), cancel))


ODDS = {
    "trio": [{"combination": "1-2-3", "odds_value": 5.5}],
    "trifecta": [{"combination": "1-2-3", "odds_value": 20.0},
                 {"combination": "2-1-3", "odds_value": 25.0}],
    "quinellaPlace": [{"combination": "1-2", "odds_value": 2.0}],
    "exacta": [{"combination": "1-2", "odds_value": 3.0}],
}


def _run(conn, clock, odds=ODDS):
    scraper = MagicMock()
    scraper.fetch_odds.return_value = odds

    @contextmanager
    def _ctx():
        yield conn
        conn.commit()

    with patch("snapshot_prestart_odds_wt.get_connection", _ctx):
        done = run(clock=clock, scraper=scraper)
    return done, scraper


def _types(conn):
    return sorted(conn.execute(
        "SELECT race_key, snapshot_type, bet_type, COUNT(*) FROM wt_odds_snapshot "
        "GROUP BY 1, 2, 3").fetchall())


def test_run_takes_the_right_type_per_race():
    conn = _db()
    _race(conn, "A", 9)      # t10
    _race(conn, "B", 5)      # t07
    _race(conn, "C", 15)     # 対象外
    _race(conn, "D", -1)     # 発走済み
    _race(conn, "E", 5, cancel=1)            # 中止
    _race(conn, "F", 5, date="2026-11-14")   # 別の日
    done, scraper = _run(conn, lambda: NOW)
    assert done == {T10: 1, T07: 1}
    assert scraper.fetch_odds.call_count == 2
    assert _types(conn) == [
        ("A", T10, "trifecta", 2), ("A", T10, "trio", 1),
        ("B", T07, "trifecta", 2), ("B", T07, "trio", 1),
    ]


def test_only_trio_and_trifecta_are_saved():
    conn = _db()
    _race(conn, "A", 9)
    _run(conn, lambda: NOW)
    bts = {r[0] for r in conn.execute("SELECT DISTINCT bet_type FROM wt_odds_snapshot")}
    assert bts == {"trio", "trifecta"}


def test_snapshot_at_is_the_actual_fetch_time():
    conn = _db()
    _race(conn, "A", 9)
    _run(conn, lambda: NOW)
    (at,) = conn.execute("SELECT DISTINCT snapshot_at FROM wt_odds_snapshot").fetchone()
    assert at == NOW.isoformat(timespec="seconds")


def test_second_run_neither_refetches_nor_overwrites():
    conn = _db()
    _race(conn, "A", 9)
    _run(conn, lambda: NOW)
    later = NOW + timedelta(seconds=60)   # まだ t10 の窓の中
    changed = {**ODDS, "trio": [{"combination": "1-2-3", "odds_value": 9.9}]}
    done, scraper = _run(conn, lambda: later, odds=changed)
    assert done == {}
    assert scraper.fetch_odds.call_count == 0
    (v,) = conn.execute("SELECT odds_value FROM wt_odds_snapshot "
                        "WHERE bet_type='trio'").fetchone()
    assert v == 5.5


def test_t07_follows_t10_for_the_same_race():
    conn = _db()
    _race(conn, "A", 9)
    _run(conn, lambda: NOW)                                  # 9分前 → t10
    done, _ = _run(conn, lambda: NOW + timedelta(minutes=3))  # 6分前 → t07
    assert done == {T07: 1}
    got = {r[0] for r in conn.execute("SELECT DISTINCT snapshot_type FROM wt_odds_snapshot")}
    assert got == {T10, T07}


def test_value_taken_after_start_is_dropped():
    """取得に時間がかかって発走を過ぎたら保存しない。"""
    conn = _db()
    _race(conn, "A", 1)
    times = iter([NOW, NOW, NOW + timedelta(minutes=2)])  # run の now / 判定 / 取得後
    done, scraper = _run(conn, lambda: next(times))
    assert scraper.fetch_odds.call_count == 1
    assert done == {}
    assert conn.execute("SELECT COUNT(*) FROM wt_odds_snapshot").fetchone()[0] == 0


def test_empty_odds_are_not_recorded_as_taken():
    """オッズが取れなかったレースは取得済みにしない（次の分に取り直せる）。"""
    conn = _db()
    _race(conn, "A", 9)
    done, _ = _run(conn, lambda: NOW, odds=None)
    assert done == {}
    done, _ = _run(conn, lambda: NOW + timedelta(seconds=60))
    assert done == {T10: 1}


def test_no_upcoming_race_touches_nothing():
    conn = _db()
    _race(conn, "A", 30)
    done, scraper = _run(conn, lambda: NOW)
    assert done == {}
    assert scraper.fetch_odds.call_count == 0
