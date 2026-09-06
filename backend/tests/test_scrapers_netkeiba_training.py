"""調教とデータ分析の解析規則を固定する。

なぜ必要か（2026-09-06・統合 Phase 2 後半）:
    どちらも「解析が静かに 0 件になる」型の壊れ方をする。特にデータ分析は、
    netkeiba が分類を増やしたときに**移設元が黙って捨てていた**実例がある
    （下記 `test_未知の見出しは捨てるが警告を出す`）。
"""

from __future__ import annotations

import logging

from src.scrapers.netkeiba.data_analysis import parse as parse_analysis
from src.scrapers.netkeiba.training import parse as parse_training

# --------------------------------------------------------------------------
# 調教
# --------------------------------------------------------------------------

# 実ページの構造を写したもの（値はダミー）。
_TRAINING_HTML = """
<table>
  <tr><th>枠</th><th>馬番</th><th>馬名</th><th>位置</th><th>脚色</th><th>評価</th></tr>
  <tr>
    <td class="Waku1">1</td>
    <td class="Umaban">1</td>
    <td class="Horse_Info fc">ダミーウマ</td>
    <td class="TrainingLoad">馬也</td>
    <td class="Training_Critic" nowrap="">好調持続</td>
    <td class="Rank_B">B</td>
  </tr>
  <tr>
    <td class="Waku2">2</td>
    <td class="Umaban">2</td>
    <td class="Horse_Info fc">ダミーウマニ</td>
    <td class="TrainingLoad">強め</td>
    <td class="Training_Critic" nowrap="">反応平凡</td>
    <td class="Rank_D">D</td>
  </tr>
</table>
"""


def test_評価語とランクを連結する():
    """DB に入る形は「評価語 + 半角スペース + ランク」（実測: 'キビキビ A'）。"""
    assert parse_training(_TRAINING_HTML) == [
        {"horse_no": 1, "training": "好調持続 B"},
        {"horse_no": 2, "training": "反応平凡 D"},
    ]


def test_調教が未公開なら空を返す():
    """空文字で上書きして既存の値を潰さないこと。"""
    assert parse_training("<table><tr><td class='Umaban'>1</td></tr></table>") == []


def test_ページ全体が無くても落ちない():
    assert parse_training("<html><body></body></html>") == []


# --------------------------------------------------------------------------
# データ分析
# --------------------------------------------------------------------------

_ANALYSIS_HTML = """
<div class="TopHorses">
  <span class="Umaban_Num">1</span>
  <span class="Umaban_Num">8</span>
  <span class="Umaban_Num">3</span>
  <span class="Umaban_Num">5</span>
</div>
<div id="RaceDataPickup01">
  <table class="PickupRaceDataTable01">
    <div class="PickupHorseTableTitle">このコースが得意な馬</div>
    <tr><td><span class="Umaban_Num">1</span></td></tr>
  </table>
  <table class="PickupRaceDataTable01">
    <div class="PickupHorseTableTitle">今回の馬場状態が得意な馬</div>
    <tr><td><span class="Umaban_Num">1</span><span class="Umaban_Num">8</span></td></tr>
  </table>
  <table class="PickupRaceDataTable01">
    <div class="PickupHorseTableTitle">今回のクッション値が得意な馬</div>
    <tr><td><span class="Umaban_Num">2</span></td></tr>
  </table>
</div>
"""


def test_データ上位馬は先頭3頭だけ():
    parsed = parse_analysis(_ANALYSIS_HTML)
    assert parsed["top_horses"] == [1, 8, 3]


def test_見出しごとに馬番を拾う():
    parsed = parse_analysis(_ANALYSIS_HTML)
    assert parsed["analysis_data"]["course_suited"]["horses"] == [1]
    assert parsed["analysis_data"]["track_condition_suited"]["horses"] == [1, 8]


def test_クッション値の分類を拾う():
    """🔴 移設元の分類に無く、**本番 DB に 1 件も入っていなかった**項目。

    2026-09-06 実測: `sekito.netkeiba_data_analysis` の直近30日に現れるキーは
    7 種類だけで、クッション値は 0 件。netkeiba が分類を増やしたのに移設元が
    黙って捨て続けていた。未知の見出しを WARNING に出すようにしたのはこのため。
    """
    parsed = parse_analysis(_ANALYSIS_HTML)
    assert parsed["analysis_data"]["cushion_suited"]["horses"] == [2]


def test_未知の見出しは捨てるが警告を出す(caplog):
    """黙って捨てると、netkeiba が分類を増やしても誰も気づけない。"""
    html = """
    <div id="RaceDataPickup01">
      <table class="PickupRaceDataTable01">
        <div class="PickupHorseTableTitle">まだ知らない分類の馬</div>
        <tr><td><span class="Umaban_Num">7</span></td></tr>
      </table>
    </div>
    """
    with caplog.at_level(logging.WARNING):
        parse_analysis(html)
    assert "まだ知らない分類の馬" in caplog.text


def test_データが無いページはNoneを返す():
    assert parse_analysis("<p>データがありません</p>") is None
    assert parse_analysis("<html><body></body></html>") is None
