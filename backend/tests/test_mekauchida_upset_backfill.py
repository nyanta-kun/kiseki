"""メカウチダ履歴ページからの波乱度取り込み（パーサと対応づけ・書き込み SQL）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from src.scrapers.mekauchida import (
    MekauchidaParseError,
    parse_history_index,
    parse_history_page,
)

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mekauchida_upset_backfill.py"
_spec = importlib.util.spec_from_file_location("mekauchida_upset_backfill", _SCRIPT)
assert _spec and _spec.loader
backfill = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = backfill  # dataclass が自分のモジュールを引くため
_spec.loader.exec_module(backfill)

# 実ページ（2026-10-18 地方・2025-09-27 中央）の見出しを写したもの
PAGE = (
    '<div class="upd">更新 10/24 15:45</div>'
    "<div class='card race'><div class='rh'><span class='rt'>14:30</span>"
    "<span class='rv'>金沢<b>4R</b></span><span class='rc'>ダ1400m</span>"
    "<span class='chip c-hard'>荒れやすさ 高</span></div>"
    "<div class='horse'><span class='num'>8</span></div></div>"
    "<div class='rest'><div class='rh'><span class='rt'>09:40</span>"
    "<span class='rv'>新潟<b>1R</b></span><span class='rc'>芝1200</span>"
    "<span class='chip c-mid'>荒れやすさ 中</span></div></div>"
    "<div class='rest'><div class='rh'><span class='rt'>10:10</span>"
    "<span class='rv'>中山<b>2R</b></span><span class='rc'>ダ1800</span></div></div>"
)


def test_履歴ページから札を読む() -> None:
    page = parse_history_page(PAGE, "20261018")
    assert page.updated == "10/24 15:45"
    assert [(r.venue, r.race_number, r.upset) for r in page.races] == [
        ("金沢", 4, "高"),
        ("新潟", 1, "中"),
        ("中山", 2, None),  # 札の無いレース（中央の 9/25 以前は買ったレースにしか無い）
    ]


def test_見出しが無いページは例外() -> None:
    with pytest.raises(MekauchidaParseError):
        parse_history_page("<html></html>", "20261018")


def test_目次から日付を昇順で読む() -> None:
    html = "<a href='20261024.html'>x</a><a href='20250927.html'>y</a><a href='20261024.html'>z</a>"
    assert parse_history_index(html) == ["20250927", "20261024"]


def test_DBのレースに対応づけ札なしと対応なしを数える() -> None:
    page = parse_history_page(PAGE, "20261018")
    rows, counts = backfill.match_rows(page, [(101, "新潟", 1), (102, "中山", 2)])
    assert rows == [{"race_id": 101, "level": "中", "site_updated": "10/24 15:45"}]
    assert counts["matched"] == 1
    assert counts["no_chip"] == 1
    assert counts["unmatched"] == 1
    assert counts["unmatched:金沢"] == 1


def test_書き込みはhistoryとして入れliveの行を上書きしない() -> None:
    sql = str(backfill.UPSERT_HISTORY_SQL)
    assert "'history'" in sql
    assert "WHERE keiba.mekauchida_race_upset.source = 'history'" in sql
