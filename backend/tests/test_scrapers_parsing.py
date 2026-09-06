"""移設したスクレイパの解析規則を固定する。

なぜ必要か（2026-09-06・統合 Phase 2）:
    穴ぐさ・吉馬のスクレイパを sekito から kiseki へ移した。両者とも
    **解析が静かに 0 件になる**壊れ方をする（サイトの HTML が変わっても
    例外は出ず、ジョブは success を返し、指数だけが欠ける）。
    [[sekito-silent-data-breakage]] の型そのものなので、解析規則そのものを
    テストで固定しておく。

フィクスチャについて:
    `tests/fixtures/scrapers/*.html` は**実ページの構造だけ**を写したもので、
    本文と数値はダミーに差し替えてある（いずれも有料サイトのコンテンツのため）。
    移設時の正しさは、本番 DB に入っている sekito の取得結果と移植版の解析結果を
    突き合わせて確認した（2026-09-06 実測: 穴ぐさ 55/55 行一致、
    吉馬 阪神1R 10/10 行一致・URL 形式も一致）。ここで固定するのは
    「その解析規則がこの先も変わらないこと」。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.scrapers.anagusa import parse as parse_anagusa
from src.scrapers.kichiuma import (
    build_url,
    parse_sp_table,
    pick_sp_table,
    safe_float,
    safe_int,
)
from src.scrapers.targets import TargetRace
from src.utils.racecourse import BY_CODE, BY_NAME

FIXTURES = Path(__file__).parent / "fixtures" / "scrapers"


# --------------------------------------------------------------------------
# 穴ぐさ
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def anagusa_records():
    html = (FIXTURES / "anagusa_list.html").read_text(encoding="utf-8")
    return parse_anagusa(html, date(2026, 9, 6))


def test_anagusa_全パネルの馬を拾う(anagusa_records):
    """場タブは CSS の切替でしかないので、1 リクエストで全場ぶんが取れる。"""
    assert len(anagusa_records) == 5
    assert {r["course_name"] for r in anagusa_records} == {"中山", "阪神", "大井"}


def test_anagusa_場名とR番号はplaceから取る(anagusa_records):
    """`.place` は "中山 7R 3歳未勝利" のように R の後ろにクラス名が続く。"""
    first = anagusa_records[0]
    assert (first["course_name"], first["race_no"]) == ("中山", 7)
    assert (first["horse_no"], first["horse_name"]) == (3, "ラクシオンデクラ")


def test_anagusa_ランクはclassから取る(anagusa_records):
    """a/b/c の class がランク。付いていない馬は '-'。"""
    by_name = {r["horse_name"]: r for r in anagusa_records}
    assert by_name["ラクシオンデクラ"]["rank"] == "A"
    assert by_name["テストホース"]["rank"] == "C"
    assert by_name["フォールバックウマ"]["rank"] == "B"
    assert by_name["ランクナシ"]["rank"] == "-"


def test_anagusa_place崩れは場タブ名へフォールバックする(anagusa_records):
    """R 番号が読めないときは 0 になる。0R は UPSERT されるが指数からは外れる。"""
    fallback = next(r for r in anagusa_records if r["horse_name"] == "フォールバックウマ")
    assert fallback["course_name"] == "阪神"  # 2 番目の .switch
    assert fallback["race_no"] == 0


def test_anagusa_場名がすべて対応表で解決できる(anagusa_records):
    """解決できない場名が出ると、その場のピックが**黙って**消える。"""
    unresolved = {r["course_name"] for r in anagusa_records if r["course_name"] not in BY_NAME}
    assert unresolved == set()


def test_anagusa_データが無いページは空を返す():
    """`.switch` が無い日は「開催なし」。例外にせず 0 件で返す。"""
    assert parse_anagusa("<html><body></body></html>", date(2026, 9, 6)) == []


# --------------------------------------------------------------------------
# 吉馬
# --------------------------------------------------------------------------

def test_kichiuma_URLは移設前と同じ形():
    """2026-09-06 に本番 URL と一致することを実測した形。

    `race_id` は 日付8桁 + レース番号2桁 + 吉馬場コード2桁 の連結。
    `date` はゼロ埋めしない（"2026/9/6"）が、`id` はゼロ埋めする。
    """
    target = TargetRace(date(2026, 9, 6), "JHSN", 1)
    assert build_url(target, BY_CODE["JHSN"].kichiuma_id) == (
        "https://kichiuma.net/php/search.php?race_id=202609060179"
        "&date=2026%2F9%2F6&no=1&id=79&p=fp"
    )


def test_kichiuma_地方は別ホスト():
    """中央 kichiuma.net / 地方 kichiuma-chiho.net。取り違えると常に空表になる。"""
    target = TargetRace(date(2026, 9, 6), "NOOI", 11)
    url = build_url(target, BY_CODE["NOOI"].kichiuma_id)
    assert url.startswith("https://kichiuma-chiho.net/php/search.php?race_id=202609061120")


@pytest.fixture(scope="module")
def kichiuma_df():
    html = (FIXTURES / "kichiuma_sp.html").read_text(encoding="utf-8")
    return pick_sp_table(html)


def test_kichiuma_SP表を列名で同定する(kichiuma_df):
    """見出しは "SP<br>能力値"。match= のパターンではパーサ次第で外すので列名で選ぶ。"""
    assert kichiuma_df is not None
    assert kichiuma_df.shape == (4, 13)


def test_kichiuma_値のない表はNoneを返す():
    assert pick_sp_table("<html><table><tr><td>1</td></tr></table></html>") is None


def test_kichiuma_行を解析して印を落とす(kichiuma_df):
    """記号列と数値列が交互に並ぶので、数値側（.1）だけを拾う。"""
    target = TargetRace(date(2026, 9, 6), "JHSN", 1)
    records = parse_sp_table(kichiuma_df, target)

    # 馬番も SP 能力値も無い行だけが落ちる。馬番だけある行（取消馬）は残す —
    # 主キーが揃うので UPSERT でき、既存行を NULL で上書きしない方が安全。
    assert len(records) == 3
    assert records[2]["horse_no"] == 3 and records[2]["sp_score"] is None
    assert records[0] == {
        "date": date(2026, 9, 6), "course_code": "JHSN", "race_no": 1,
        "horse_no": 1, "sp_score": 78.0, "senko": 77.0,
        "sp_trust": 81.0, "sp_adjust": 81.0, "sp_max": 82.0, "sueashi": 88.0,
    }
    assert records[1]["horse_no"] == 2
    assert records[1]["sp_score"] == 63.3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("78.0", 78.0),
        ("▲66", 66.0),
        ("－", None),
        ("---", None),
        ("", None),
        (None, None),
        ("SP 72.8 点", 72.8),
    ],
)
def test_kichiuma_safe_float(raw, expected):
    assert safe_float(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("11", 11), ("◎3", 3), ("－", None), ("", None), (None, None)],
)
def test_kichiuma_safe_int(raw, expected):
    assert safe_int(raw) == expected


# --------------------------------------------------------------------------
# 実行結果の集計
# --------------------------------------------------------------------------

def test_スキップは失敗ではない():
    """🔴 吉馬は 1 日 2 回走らせる（00:30 と 06:30）。

    2 回目は前の回で取れたぶんが `should_fetch` に弾かれるので、**取りこぼしが
    無い日ほどスキップだらけになる**。ここを「成功 0 件なら異常」と数えると、
    正常な日に毎晩 cron がエラーを吐き続けることになる。
    """
    from src.scrapers.kichiuma import ScrapeResult

    全部スキップ = ScrapeResult(success=0, skipped=79, errors=0)
    assert 全部スキップ.handled == 79

    全滅 = ScrapeResult(success=0, skipped=0, errors=79)
    assert 全滅.handled == 0

    一部成功 = ScrapeResult(success=3, skipped=76, errors=0)
    assert 一部成功.handled == 79
