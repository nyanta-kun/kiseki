"""血統ページの解析と埋め戻しの対象選択を固定する。

なぜ必要か（2026-09-06）:
    血統は日次の取得対象から外してある（JRA-VAN が上位互換・実測一致率 99.6%）。
    残しているのは**埋め戻し専用**で、中央登録歴の無い地方専用馬の父・母父が
    netkeiba にしか無いため。使う頻度が低いぶん、壊れても気づきにくい。
"""

from __future__ import annotations

from src.scrapers.netkeiba.backfill import TARGETS
from src.scrapers.netkeiba.blood import parse

# 実ページの構造を写したもの（値はダミー）。
_BLOOD_HTML = """
<table class="RaceCommon_Table Bias RaceTable01 ShutubaTable">
  <tr><th>枠</th><th>馬番</th><th>印</th><th>馬名</th><th>父名</th><th>母父名</th></tr>
  <tr>
    <td class="Num Waku1">1</td>
    <td class="UmaBan Num">1</td>
    <td class="CheckMark Horse_Select">印</td>
    <td class="Horse_Name">ダミーウマ</td>
    <td class="Blood_Cell">ダミーチチ</td>
    <td class="Blood_Cell">ダミーハハチチ</td>
  </tr>
  <tr>
    <td class="Num Waku2">2</td>
    <td class="UmaBan Num">2</td>
    <td class="CheckMark Horse_Select">印</td>
    <td class="Horse_Name">ダミーウマニ</td>
    <td class="Blood_Cell">ダミーチチニ</td>
    <td class="Blood_Cell">ダミーハハチチニ</td>
  </tr>
</table>
"""


def test_父と母父を文書順で拾う():
    """🔴 `Blood_Cell` は 2 つ並ぶだけで、クラスでは父・母父を区別できない。

    ヘッダが「父名」「母父名」の順なので、**1 つ目が父・2 つ目が母父**。
    逆にすると入れ替わったまま静かに保存される。
    """
    assert parse(_BLOOD_HTML) == [
        {"horse_no": 1, "sire": "ダミーチチ", "broodmare_sire": "ダミーハハチチ"},
        {"horse_no": 2, "sire": "ダミーチチニ", "broodmare_sire": "ダミーハハチチニ"},
    ]


def test_血統セルが足りない行は捨てる():
    html = """
    <table class="Bias">
      <tr><td class="UmaBan">1</td><td class="Blood_Cell">父だけ</td></tr>
    </table>
    """
    assert parse(html) == []


def test_血統表が無いページは空を返す():
    assert parse("<html><body></body></html>") == []


def test_埋め戻しの対象は3種類():
    """馬名と評価は JV-Link / 逆変換で直したので、再取得が要るのはこの 3 つだけ。"""
    assert set(TARGETS) == {"blood", "training", "paddock"}
