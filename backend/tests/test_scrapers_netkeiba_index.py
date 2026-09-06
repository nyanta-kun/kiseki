"""netkeiba タイム指数の解析と race_id 組み立てを固定する。

なぜ必要か（2026-09-06・統合 Phase 2 後半）:
    移設元は「pandas で読む → 値が違うので BeautifulSoup で読み直して上書きする」
    という二重構造で、中央と地方に別々の列マッピングを持っていた。実ページを
    調べたところ、その複雑さの原因は**並べ替え用の隠しスパンを外していなかったこと**
    だった。外せば中央・地方とも同じ並びで素直に読める。

    ここで固定するのは、その「素直な読み方」が壊れないこと。特に

      1. 隠しスパンを外すこと（外さないと "1081"+"81" が "108181" になる）
      2. データ行の並び（ヘッダは colspan で 14 列、データ行は 16 セル）
      3. race_id の組み立て（中央だけ開催回・日目が要る）

    どれも例外を出さずに壊れる。
"""

from __future__ import annotations

from datetime import date

import pytest
from bs4 import BeautifulSoup

from src.scrapers.netkeiba import race_id as rid
from src.scrapers.netkeiba.time_index import (
    cell_text,
    classify_unavailability,
    parse,
)

# --------------------------------------------------------------------------
# 隠しスパン
# --------------------------------------------------------------------------

def _cell(html: str):
    return BeautifulSoup(html, "html.parser").find("td")


def test_並べ替え用の隠し値を外して表示値だけ返す():
    """🔴 外さないと "1081" + "81" が連結されて "108181" になる。

    移設元にあった「文字列を半分に割って前後が同じなら片方を捨てる」
    「スペース区切りの最後を採る」といった後処理は、この帳尻合わせだった。
    """
    td = _cell(
        '<td class="sk__max_index">'
        '<span class="Sort_Function_Data_Hidden">1081</span>'
        '<a href="https://db.netkeiba.com/race/x">81</a></td>'
    )
    assert cell_text(td) == "81"


def test_馬名の重複も隠しスパンが原因():
    td = _cell(
        '<td class="Horse_Name sk__horse_name">'
        '<span class="Sort_Function_Data_Hidden">ルースソラール</span>'
        '<a href="x">ルースソラール</a></td>'
    )
    assert cell_text(td) == "ルースソラール"


def test_隠しスパンが無いセルはそのまま():
    assert cell_text(_cell('<td class="sk__average_index">79*</td>')) == "79*"


# --------------------------------------------------------------------------
# 表の解析
# --------------------------------------------------------------------------

# 実ページの構造を写したもの（値はダミー）。
# ヘッダは 14 列（「近走成績」が colspan=3）だが、データ行は 16 セル。
# **ヘッダの列数でデータ列を数えてはいけない**ことをここで固定する。
_SPEED_HTML = """
<table>
  <tr><th>枠</th><th>馬番</th><th>印</th><th>馬名</th><th>性齢</th><th>斤量</th>
      <th>騎手</th><th>最高</th><th>５走平均</th><th>距離</th><th>コ｜ス</th>
      <th colspan="3">近走成績</th><th>単勝オッズ</th><th>人気</th></tr>
  <tr><th>3走</th><th>2走</th><th>前走</th></tr>
  <tr>
    <td>1</td>
    <td class="UmaBan sk__umaban"><div>1</div></td>
    <td>印</td>
    <td class="Horse_Name sk__horse_name">
      <span class="Sort_Function_Data_Hidden">ダミーウマ</span><a href="x">ダミーウマ</a></td>
    <td>牡2</td><td class="sk__load_weight">55.0</td><td>騎手名</td>
    <td class="sk__max_index"><span class="Sort_Function_Data_Hidden">1081</span><a href="x">81</a></td>
    <td class="sk__average_index"><span class="Sort_Function_Data_Hidden">1079</span>79*</td>
    <td class="sk__max_distance_index"><span class="Sort_Function_Data_Hidden">1081</span><a href="x">81</a></td>
    <td class="sk__max_course_index"><span class="Sort_Function_Data_Hidden">1076</span><a href="x">76</a></td>
    <td class="sk__index3"><span class="Sort_Function_Data_Hidden">1000</span>-</td>
    <td class="sk__index2"><span class="Sort_Function_Data_Hidden">1076</span><a href="x">76</a></td>
    <td class="sk__index1"><span class="Sort_Function_Data_Hidden">1081</span><a href="x">81</a></td>
    <td class="sk__odds">2.4</td><td class="sk__ninki">1</td>
  </tr>
</table>
"""


def test_実ページの並びで解析できる():
    records = parse(_SPEED_HTML)
    assert records == [{
        "horse_no": 1, "horse_name": "ダミーウマ",
        "idx_max": "81", "idx_ave": "79*", "idx_distance": "81",
        "idx_course": "76", "idx_third": "-", "idx_second": "76", "idx_last": "81",
    }]


def test_表が無ければ空を返す():
    assert parse("<html><body><p>タイム指数はございません</p></body></html>") == []


# --------------------------------------------------------------------------
# 未公開 / 不在の切り分け
# --------------------------------------------------------------------------

def test_発走まで時間があれば未公開扱い():
    """再試行間隔が違う（未公開は 1 時間、不在は 6 時間）。"""
    from datetime import datetime, timedelta

    from src.scrapers.netkeiba.time_index import JST
    future = datetime.now(JST) + timedelta(hours=5)
    assert classify_unavailability("タイム指数はございません", future) == "not_yet_published"


def test_レース後24時間以上なら恒久的に不在():
    from datetime import datetime, timedelta

    from src.scrapers.netkeiba.time_index import JST
    past = datetime.now(JST) - timedelta(hours=30)
    assert classify_unavailability("タイム指数はございません", past) == "not_available"


def test_発走時刻不明なら安全側に倒す():
    assert classify_unavailability("タイム指数はございません", None) == "not_yet_published"


# --------------------------------------------------------------------------
# race_id
# --------------------------------------------------------------------------

def test_中央のrace_idはjravan_race_idから開催回と日目を取る():
    """2026-09-06 実測: `sekito.kaisai` と 54/54 一致・不一致 0。

    jravan_race_id は YYYY(4)+MMDD(4)+場(2)+開催回(2)+日目(2)+R(2) の 16 文字。
    """
    got = rid.jra_race_id(date(2026, 9, 6), "09", 1, "2026090609040201")
    assert got == "202609040201"


def test_開催回が取れないrace_idは例外にする():
    """組み立ててしまうと、存在しない race_id で叩き続けることになる。"""
    with pytest.raises(ValueError):
        rid.jra_race_id(date(2026, 9, 6), "09", 1, "20260906")


def test_地方のrace_idは月日を使う():
    assert rid.nar_race_id(date(2026, 9, 6), "54", 1) == "202654090601"


def test_取得先ホストが中央と地方で分かれる():
    assert rid.time_index_url("x", is_jra=True).startswith("https://race.netkeiba.com/")
    assert rid.time_index_url("x", is_jra=False).startswith("https://nar.netkeiba.com/")
    # 調教とパドックは中央のみ
    assert rid.training_url("x").startswith("https://race.netkeiba.com/")
    assert rid.paddock_url("x").startswith("https://race.netkeiba.com/")
