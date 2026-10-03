"""メカウチダのパーサと通知判定のテスト。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.scrapers.mekauchida import MekauchidaParseError, parse_today_page
from src.services.mekauchida_notify import (
    JST,
    HorseContext,
    build_message,
    is_final_poll,
    is_monitoring,
    match_race,
    match_reasons,
    parse_post_at,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mekauchida_index_20261003_1500.html"
NAR_FIXTURE = Path(__file__).parent / "fixtures" / "mekauchida_nar_index_20261003_1503.html"


@pytest.fixture(scope="module")
def page():
    return parse_today_page(FIXTURE.read_text(encoding="utf-8"), year=2026)


def test_page_header(page):
    assert page.date == "20261003"
    assert page.updated == "10/03 15:00"
    assert len(page.races) == 24


def test_card_states(page):
    states = {(r.venue, r.race_number): r.state for r in page.races}
    assert states[("京都", 1)] == "decided"
    assert states[("東京", 4)] == "missed"  # 障害
    assert states[("東京", 10)] == "wait"


def test_picks_buy_and_provisional(page):
    picks = {(r.venue, r.race_number): r.picks for r in page.races if r.picks}
    assert set(picks) == {("京都", 4), ("東京", 9), ("東京", 11), ("京都", 12), ("東京", 12)}

    (buy,) = picks[("京都", 4)]
    assert (buy.horse_number, buy.horse_name, buy.kind) == (8, "デラスゴイガン", "buy")
    assert buy.flags == ("A", "B")
    assert (buy.popularity, buy.win_odds, buy.expected_value) == (4, 6.4, 1.0)
    assert (buy.place_odds_low, buy.place_odds_high) == (2.0, 2.7)

    (pv,) = picks[("東京", 11)]
    assert (pv.horse_number, pv.kind, pv.flags) == (11, "pv", ("B",))
    # 見込みは「09:00時点」の行（判断の根拠）を使い、「今」の行は使わない
    assert (pv.popularity, pv.place_odds_high) == (3, 3.0)

    (no_flag,) = picks[("京都", 12)]
    assert no_flag.flags == ()


def test_detail_table_numbers_are_not_picks(page):
    """予想の詳細表（全馬の馬番）を買い目として拾わない。"""
    kyoto1 = next(r for r in page.races if (r.venue, r.race_number) == ("京都", 1))
    assert kyoto1.picks == ()


def test_date_falls_back_to_title_after_last_decision():
    html = FIXTURE.read_text(encoding="utf-8")
    html = html.replace("<div class='next' data-date='2026-10-03'", "<div class='gone'")
    assert parse_today_page(html, year=2026).date == "20261003"


def test_structure_change_raises():
    with pytest.raises(MekauchidaParseError):
        parse_today_page("<html><div class='next' data-date='2026-10-03'></div></html>", 2026)
    html = FIXTURE.read_text(encoding="utf-8").replace("<span class='res buy'>", "<span class='r'>")
    with pytest.raises(MekauchidaParseError):
        parse_today_page(html, 2026)


def _at(hhmm: str, sec: int = 0) -> datetime:
    return datetime(2026, 10, 3, int(hhmm[:2]), int(hhmm[2:]), sec, tzinfo=JST)


@pytest.mark.parametrize(
    ("now", "monitoring", "final"),
    [
        (_at("1444"), False, False),  # 61 分前
        (_at("1445"), True, False),  # ちょうど 60 分前
        (_at("1534", 59), True, False),  # 10 分 1 秒前
        (_at("1535"), True, True),  # ちょうど 10 分前
        (_at("1535", 4), True, True),  # cron の起動遅れ
        (_at("1544", 59), True, True),
        (_at("1545"), False, False),  # 発走時刻ちょうど＝通知しない
        (_at("1547"), False, False),  # 発走後
    ],
)
def test_windows(now, monitoring, final):
    post = parse_post_at("20261003", "1545")
    assert is_monitoring(post, now) is monitoring
    assert is_final_poll(post, now) is final


def test_exactly_one_final_poll_on_ten_minute_grid():
    """10 分おきの監視では、どの発走時刻でも最後の監視がちょうど 1 回になる。"""
    base = datetime(2026, 10, 3, 13, 0, 3, tzinfo=JST)  # cron は数秒遅れて起動する
    polls = [base + timedelta(minutes=m) for m in range(0, 180, 10)]
    for post_min in range(60, 120):
        post = datetime(2026, 10, 3, 13, 0, tzinfo=JST) + timedelta(minutes=post_min)
        assert sum(is_final_poll(post, p) for p in polls) == 1, post


def test_parse_post_at_invalid():
    assert parse_post_at("20261003", None) is None
    assert parse_post_at("20261003", "15:4") is None
    assert parse_post_at("20261003", "2599") is None


def test_match_reasons():
    assert match_reasons(None) == ()
    assert match_reasons(HorseContext(1, 5, 60.0, None)) == ("指数5位",)
    assert match_reasons(HorseContext(1, 6, 50.0, None)) == ()
    assert match_reasons(HorseContext(1, 9, 40.0, "C")) == ("穴ぐさC",)
    assert match_reasons(HorseContext(1, 2, 60.0, "A")) == ("指数2位", "穴ぐさA")
    assert match_reasons(HorseContext(1, None, None, None)) == ()


def test_match_race_and_message(page):
    race = next(r for r in page.races if (r.venue, r.race_number) == ("東京", 11))
    horses = {11: HorseContext(11, 4, 57.9, None), 3: HorseContext(3, 1, 63.0, "A")}
    matches = match_race(race, horses)
    assert [m.pick.horse_number for m in matches] == [11]
    msg = build_message(matches, _at("1540"), "見出し")
    assert "東京11R" in msg and "11番 ジャスティンアース[B]（見込み）" in msg and "指数4位" in msg

    assert match_race(race, {11: HorseContext(11, 6, 50.0, None)}) == []


def test_nar_page():
    """地方版も同じ構造で読める（複勝は「複 4.5〜」と上限なし）。"""
    page = parse_today_page(NAR_FIXTURE.read_text(encoding="utf-8"), year=2026)
    assert page.date == "20261003"
    assert len(page.races) == 12
    assert {r.venue for r in page.races} == {"高知"}
    (pick,) = next(r for r in page.races if r.race_number == 7).picks
    assert (pick.horse_number, pick.horse_name, pick.kind, pick.flags) == (12, "ブーバー", "pv", ())
    assert (pick.popularity, pick.win_odds, pick.expected_value) == (2, 4.6, 1.24)
    assert (pick.place_odds_low, pick.place_odds_high) == (4.5, None)
    race = next(r for r in page.races if r.race_number == 7)
    msg = build_message(match_race(race, {12: HorseContext(12, 2, 68.0, None)}), _at("1812"), "地方")
    assert "複4.5〜" in msg and "指数2位" in msg


def test_settled_buy_card_is_still_a_pick():
    """精算後は res buy が res hit 等に変わる。買い目として読み続ける。"""
    html = FIXTURE.read_text(encoding="utf-8").replace("<span class='res buy'>", "<span class='res hit'>")
    page = parse_today_page(html, year=2026)
    (pick,) = next(r for r in page.races if (r.venue, r.race_number) == ("京都", 4)).picks
    assert pick.kind == "buy"
