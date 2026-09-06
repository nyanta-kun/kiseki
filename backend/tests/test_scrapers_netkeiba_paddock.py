"""パドックの解析規則を固定する。

なぜ必要か（2026-09-06）:
    移設元 `bin/scrape/netkeiba-paddock` は 2 つの不具合を抱えていた。
    どちらも例外を出さず、**success を返しながら**壊れる形だった。

      1. 文字コードを EUC-JP に決め打ち。paddock.html は UTF-8 なので必ず化け、
         しかも UPSERT が正しい馬名を上書きしていた（実測 192/192 行）。
      2. 評価「穴」が非 ASCII なので化け、kiseki のスコア表のキーに一致せず
         **中立 50.0 に潰れていた**（実測 26 行）。

    ここで固定するのは、解析が実ページの構造に沿っていることと、
    想定外の評価が来たら黙らないこと。
"""

from __future__ import annotations

import logging

from src.scrapers.netkeiba.paddock import VALID_RANKS, parse
from src.scrapers.netkeiba.store import TARGET_COLUMNS

# 実ページの構造を写したもの（本文はダミー）。
_PADDOCK_HTML = """
<table class="Paddock_Table race_table_01">
  <tr><th>枠</th><th>馬番</th><th>馬名</th><th>評価</th><th>コメント</th></tr>
  <tr>
    <td class="Waku1">1</td><td class="Waku">1</td>
    <td class="Horse_Name Txt_L">ダミーウマ</td>
    <td class="Hyoka text-center">A</td>
    <td class="Comment Txt_L">馬体増は気にならない。</td>
  </tr>
  <tr>
    <td class="Waku3">3</td><td class="Waku">3</td>
    <td class="Horse_Name Txt_L">ダミーウマニ</td>
    <td class="Hyoka text-center">B</td>
    <td class="Comment Txt_L">歩様に軽さある。</td>
  </tr>
</table>
<table class="Paddock_Table race_table_01">
  <tr><th>枠</th><th>馬番</th><th>馬名</th><th>評価</th><th>コメント</th></tr>
  <tr>
    <td class="Waku5">5</td><td class="Waku">5</td>
    <td class="Horse_Name Txt_L">ダミーウマサン</td>
    <td class="Hyoka text-center">穴</td>
    <td class="Comment Txt_L">気配は上向き。</td>
  </tr>
</table>
"""


def test_1つ目のテーブルが人気_2つ目が特注():
    """p_type はテーブルの順番から決まる（ページの文字列ではない）。

    移設元も同じ規則で、Python 側のリテラルだったため p_type だけは
    文字化けを免れていた。
    """
    records = parse(_PADDOCK_HTML)
    assert [r["p_type"] for r in records] == ["人気", "人気", "特注"]


def test_馬番_評価_寸評を拾う():
    records = parse(_PADDOCK_HTML)
    assert records[0] == {
        "horse_no": 1, "horse_name": "ダミーウマ", "p_rank": "A",
        "p_comment": "馬体増は気にならない。", "p_type": "人気",
    }


def test_穴の評価を拾える():
    """🔴 '穴' は非 ASCII。移設元は EUC-JP 決め打ちで化け、

    kiseki のスコア表 `("特注","穴"): 60.0` のキーに一致せず中立 50.0 に
    潰れていた（実測 26 行・2026-08-22 以降の全期間）。
    """
    records = parse(_PADDOCK_HTML)
    ana = [r for r in records if r["p_rank"] == "穴"]
    assert len(ana) == 1
    assert ana[0]["p_type"] == "特注"


def test_想定外の評価は警告を出す(caplog):
    """文字化けや構造変化に気づけるようにする。黙って通すと指数が中立に潰れる。"""
    html = _PADDOCK_HTML.replace(
        '<td class="Hyoka text-center">A</td>',
        '<td class="Hyoka text-center">腥�</td>',
    )
    with caplog.at_level(logging.WARNING):
        parse(html)
    assert "想定外のパドック評価" in caplog.text


def test_評価の語彙は4種類():
    assert set(VALID_RANKS) == {"A", "B", "C", "穴"}


def test_パドックが無いページは空を返す():
    assert parse("<html><body></body></html>") == []


def test_パドックは馬名を書かない():
    """🔴 2026-09-06 の障害の構造的な再発防止。

    馬名の持ち主は time_index であって paddock ではない。書き込み対象の列から
    外しておけば、パドック側が何を解析しようと馬名を壊せない。
    """
    assert "horse_name" not in TARGET_COLUMNS["paddock"]
    assert "horse_name" in TARGET_COLUMNS["time_index"]
