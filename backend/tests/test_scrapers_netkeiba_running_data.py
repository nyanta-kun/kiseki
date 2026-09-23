"""netkeiba 走行データページの解析規則を固定する。

なぜ必要か:

1. 🔴 **契約による取りこぼしを「取れた」と誤認しないこと**。マスターコース契約が
   無いとサーバは1着馬だけを返す。`is_master` を読み違えると、勝った馬だけの
   偏った標本を「全頭ぶん」として貯めてしまう（例外は出ない）
2. **未収録の項目がゼロで返る**。完歩ピッチは2024年以降の重賞のみで、それ以外は
   全区間 `"0.000"`。そのまま入れると「ピッチ 0 秒」という嘘の値になる
3. 走行距離の表は (距離, 位置) が交互に並ぶ。**位置セルを距離として数えると
   ハロンごとの距離が倍の本数になる**

HTML は実ページの構造を写したもの（値はダミー）。
"""

from __future__ import annotations

from datetime import date, datetime

from src.scrapers.netkeiba.running_data import JST, is_published, parse, publication_at

_MASTER_FLAG = """
<script>
document.documentElement.classList.toggle('is-master', '{flag}' === '1');
</script>
"""

_SCRIPTS = """
<script>
const race_rap = $.parseJSON('[[1200,"12.3"],[1000,"11.2"],[800,"11.5"],[600,"11.8"],[400,"12.0"],[200,"12.4"]]');
const horse_laptime = $.parseJSON('[{"Wakuban":5,"Umaban":7,"laptime":[[1200,"12.5"],[1000,"11.4"],[800,"11.6"],[600,"11.7"],[400,"11.9"],[200,"12.2"]]},{"Wakuban":1,"Umaban":2,"laptime":[[1200,"12.9"],[1000,"11.8"],[800,"11.7"],[600,"11.6"],[400,"11.8"],[200,"12.1"]]}]');
const horse_pitch = $.parseJSON('[{"Wakuban":5,"Umaban":7,"PitchCount":[[1200,"0.000"],[1000,"0.000"],[800,"0.000"],[600,"0.000"],[400,"0.000"],[200,"0.000"]]}]');
const horse_stride = $.parseJSON('[{"Wakuban":5,"Umaban":7,"stride":[[1200,"6.80"],[1000,"7.10"],[800,"7.20"],[600,"7.15"],[400,"7.05"],[200,"6.90"]]}]');
</script>
"""

_MILAGE = """
<table id="milage_summary">
 <thead><tr><th>着順</th><th>馬番</th><th>馬名</th><th>走行距離</th><th>走破タイム</th><th>距離補正走破タイム</th></tr></thead>
 <tbody>
  <tr>
   <td>1</td><td>7</td><td>ダミーウマ</td><td>1,207.1m</td><td>1:11.3</td><td>1:11.0</td>
   <td class="CellDataWrap">201.9</td><td class="PositionCell" data-position="2">2</td>
   <td class="CellDataWrap">201.2</td><td class="PositionCell" data-position="2">2</td>
   <td class="CellDataWrap">200.0</td><td class="PositionCell" data-position="-1">-1</td>
  </tr>
  <tr>
   <td>2</td><td>2</td><td>ダミーウマニ</td><td>1,201.4m</td><td>1:11.5</td><td>1:11.4</td>
   <td class="CellDataWrap">200.5</td><td class="PositionCell" data-position="1">1</td>
   <td class="CellDataWrap">200.4</td><td class="PositionCell" data-position="1">1</td>
   <td class="CellDataWrap">200.5</td><td class="PositionCell" data-position="1">1</td>
  </tr>
 </tbody>
</table>
"""

_MASTER_PAGE = _MASTER_FLAG.format(flag=1) + _SCRIPTS + _MILAGE
_GATED_PAGE = _MASTER_FLAG.format(flag=0) + """
<script>
const race_rap = $.parseJSON('[[1200,"12.3"],[1000,"11.2"],[800,"11.5"],[600,"11.8"],[400,"12.0"],[200,"12.4"]]');
const horse_laptime = $.parseJSON('[{"Wakuban":5,"Umaban":7,"laptime":[[1200,"12.5"],[1000,"11.4"],[800,"11.6"],[600,"11.7"],[400,"11.9"],[200,"12.2"]]}]');
</script>
<p class="Master_Regist_Msg01">マスターコースなら全ての競走馬の</p>
"""


def test_マスターコースなら全頭ぶん取れる():
    d = parse(_MASTER_PAGE)
    assert d.is_master is True
    assert d.n_horses == 2
    assert sorted(d.horses) == [2, 7]


def test_契約が無いと1着馬だけしか入らない():
    """🔴 これを「取れた」と扱うと、勝った馬だけの標本を貯めてしまう。"""
    d = parse(_GATED_PAGE)
    assert d.is_master is False
    assert d.n_horses == 1


def test_レースラップはスタート側から並ぶ():
    """JRA-VAN の lap_times と同じ並び（2026-09-06 の 35 レースで一致を実測）。"""
    assert parse(_MASTER_PAGE).race_laps == [12.3, 11.2, 11.5, 11.8, 12.0, 12.4]


def test_個別ラップと枠番を馬番ごとに取る():
    h = parse(_MASTER_PAGE).horses[7]
    assert h.frame_number == 5
    assert h.laps == [12.5, 11.4, 11.6, 11.7, 11.9, 12.2]
    assert sum(h.laps) == h.finish_time   # 走行距離の表の走破タイム 71.3 と一致する


def test_未収録のピッチはゼロ埋めなので落とす():
    """完歩ピッチは2024年以降の重賞のみ。0 を値として保存しない。"""
    h = parse(_MASTER_PAGE).horses[7]
    assert h.pitch is None
    assert h.stride == [6.80, 7.10, 7.20, 7.15, 7.05, 6.90]


def test_走行距離と補正タイムを取る():
    h = parse(_MASTER_PAGE).horses[7]
    assert h.running_distance == 1207.1
    assert h.finish_time == 71.3
    assert h.corrected_time == 71.0


def test_位置セルを距離として数えない():
    """(距離, 位置) が交互に並ぶ。ラチ内側は負の値になりうる。"""
    h = parse(_MASTER_PAGE).horses[7]
    assert h.furlong_distances == [201.9, 201.2, 200.0]
    assert h.positions == [2.0, 2.0, -1.0]


def test_空のページでも落ちない():
    d = parse("<html><body></body></html>")
    assert d.is_master is False
    assert d.race_laps is None
    assert d.n_horses == 0


def test_未公開なら_published_が偽():
    page = _MASTER_FLAG.format(flag=1) + \
        "<p>レース翌週の金曜日18時頃に公開予定です。</p>"
    assert parse(page).published is False


# --------------------------------------------------------------------------
# 公開時刻
# --------------------------------------------------------------------------


def test_公開はレース後で最初の金曜18時():
    """実測: 9/6(土)のレースは 9/15 時点で公開済み・9/13(土)は未公開。"""
    assert publication_at(date(2026, 9, 6)) == datetime(2026, 9, 11, 18, 0, tzinfo=JST)
    assert publication_at(date(2026, 9, 13)) == datetime(2026, 9, 18, 18, 0, tzinfo=JST)


def test_レースが金曜なら翌週の金曜():
    """当日の18時に出るわけではない。0 日後にならないこと。"""
    assert publication_at(date(2026, 9, 11)) == datetime(2026, 9, 18, 18, 0, tzinfo=JST)


def test_公開判定は18時ちょうどを含む():
    d = date(2026, 9, 6)
    assert is_published(d, datetime(2026, 9, 11, 17, 59, tzinfo=JST)) is False
    assert is_published(d, datetime(2026, 9, 11, 18, 0, tzinfo=JST)) is True


def test_公開判定は実測と合う():
    """2026-09-15 時点で 9/6 は公開済み・9/13 は未公開（37ページで実測）。"""
    now = datetime(2026, 9, 15, 20, 0, tzinfo=JST)
    assert is_published(date(2026, 9, 6), now) is True
    assert is_published(date(2026, 9, 13), now) is False
