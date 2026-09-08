"""POG 馬一覧（netkeiba）の解析規則と世代ガードを固定する。

なぜ必要か（2026-09-08・統合 Phase 5 の 5a）:
    移設元 `bin/scrape/netkeiba-horses-bulk` は「静かに間違う」作りを3つ持っていた。
    移植版で直したので、戻らないようにここで固定する。

    1. 馬齢を `2026 - 生産年` と**年を固定で書いていた**。翌年から黙って別世代を取る
    2. netkeiba の馬ID の先頭4桁を生産年とみなせる前提。**外国産馬は英数字**
       （実測 `000a02d612`）で成り立たない
    3. 馬名を `name[:15]` と切り詰めていた。実ページの1ページ100頭中 11頭が15文字超

    加えて、父・母・母父のセルには詳細リンクのアイコンが同居しており、セル全体の
    テキストを取ると `Palace Pier[]` になる。
"""

from __future__ import annotations

from datetime import date

from src.scrapers.netkeiba.pog_horse_list import (
    HorseRow,
    horse_age,
    parse_rows,
    parse_total,
)

# 実ページ（db.netkeiba.com/horse/list.html?age_f=2&age_t=2&limit=100）の構造を
# 写したもの。値は実データだが3行に絞ってある。
_LIST_HTML = """
<div>7,943件中&nbsp;&nbsp;1から100件目</div>
<table class="nk_tb_common race_table_01 horse_list_table">
  <tr class="txt_c">
    <th nowrap="nowrap"><img src="fav.png"/></th>
    <th id="as-name" nowrap="nowrap">馬名<br/>
      <span class="sort-icon">↑</span><span class="sort-icon">↓</span></th>
    <th id="as-sex" nowrap="nowrap">性<br/>
      <span class="sort-icon">↑</span><span class="sort-icon">↓</span></th>
    <th id="as-age" nowrap="nowrap">生年<br/>
      <span class="sort-icon">↑</span><span class="sort-icon">↓</span></th>
    <th nowrap="nowrap"></th>
    <th id="as-trainer" nowrap="nowrap">厩舎<br/>
      <span class="sort-icon">↑</span><span class="sort-icon">↓</span></th>
    <th id="as-sire" nowrap="nowrap">父<br/>
      <span class="sort-icon">↑</span><span class="sort-icon">↓</span></th>
    <th id="as-mare" nowrap="nowrap">母<br/>
      <span class="sort-icon">↑</span><span class="sort-icon">↓</span></th>
    <th id="as-bms" nowrap="nowrap">母父<br/>
      <span class="sort-icon">↑</span><span class="sort-icon">↓</span></th>
    <th id="as-owner" nowrap="nowrap">馬主<br/>
      <span class="sort-icon">↑</span><span class="sort-icon">↓</span></th>
  </tr>
  <tr>
    <td><input type="checkbox" value="000a02d612"/></td>
    <td class="bml txt_l">
      <a href="https://db.netkeiba.com/horse/000a02d612/" title="Alpha">Alpha</a></td>
    <td class="txt_c">牝</td>
    <td class="txt_c"><a href="https://db.netkeiba.com/horse/list.html?year=2024">2024</a></td>
    <td class="txt_l"><a href="https://db.netkeiba.com/horse/ped/000a02d612/">[血]</a></td>
    <td class="txt_l"></td>
    <td class="txt_l df_sbw w_horse"><div>
      <a href="https://db.netkeiba.com/horse/list.html?sire_id=x" title="Sea The Stars">Sea The Stars</a>
      <a class="link-detail" href="https://db.netkeiba.com/horse/sire/x/" title="Sea The Stars">
        <span>[</span><img src="i.png"/><span>]</span></a></div></td>
    <td class="txt_l df_sbw w_horse"><div>
      <a href="https://db.netkeiba.com/horse/list.html?mare_id=y" title="Alpha Centauri">Alpha Centauri</a>
      <a class="link-detail" href="https://db.netkeiba.com/horse/mare/y/" title="Alpha Centauri">
        <span>[</span><img src="i.png"/><span>]</span></a></div></td>
    <td class="txt_l df_sbw w_horse"><div>
      <a href="https://db.netkeiba.com/horse/list.html?bms_id=z" title="Mastercraftsman">Mastercraftsman</a></div></td>
    <td class="txt_l"></td>
  </tr>
  <tr>
    <td><input type="checkbox" value="2024108278"/></td>
    <td class="bml txt_l">
      <a href="https://db.netkeiba.com/horse/2024108278/" title="Angel Numberの2024">Angel Numberの2024</a></td>
    <td class="txt_c">牝</td>
    <td class="txt_c"><a href="https://db.netkeiba.com/horse/list.html?year=2024">2024</a></td>
    <td class="txt_l"><a href="https://db.netkeiba.com/horse/ped/2024108278/">[血]</a></td>
    <td class="txt_l"><a href="https://db.netkeiba.com/trainer/01234/">[西] 矢作芳人</a></td>
    <td class="txt_l df_sbw w_horse"><div>
      <a href="https://db.netkeiba.com/horse/list.html?sire_id=a" title="Twirling Candy">Twirling Candy</a></div></td>
    <td class="txt_l df_sbw w_horse"><div>
      <a href="https://db.netkeiba.com/horse/list.html?mare_id=b" title="Angel Number">Angel Number</a></div></td>
    <td class="txt_l df_sbw w_horse"><div>
      <a href="https://db.netkeiba.com/horse/list.html?bms_id=c" title="Lemon Drop Kid">Lemon Drop Kid</a></div></td>
    <td class="txt_l"><a href="https://db.netkeiba.com/owner/999/">キャロットファーム</a></td>
  </tr>
</table>
"""


def test_総件数を読む():
    assert parse_total(_LIST_HTML) == 7943


def test_総件数が無ければ0を返す():
    assert parse_total("<html><body>該当データはありません</body></html>") == 0


def test_行を解析する():
    rows = parse_rows(_LIST_HTML)
    assert len(rows) == 2
    assert rows[0] == HorseRow(
        netkeiba_horse_id="000a02d612",
        name="Alpha",
        sex="牝",
        birth_year=2024,
        sire="Sea The Stars",
        broodmare="Alpha Centauri",
        broodmare_sire="Mastercraftsman",
        stable=None,
        owner=None,
    )


def test_外国産馬のIDは英数字なので先頭4桁を生産年に使えない():
    """移設元は netkeiba の馬ID を数字10桁と決めつけていた。"""
    rows = parse_rows(_LIST_HTML)
    foreign = rows[0]
    assert not foreign.netkeiba_horse_id.isdigit()
    # それでも生産年は「生年」列から取れている
    assert foreign.birth_year == 2024


def test_馬名を切り詰めない():
    """移設元は name[:15]。実ページでは1ページ100頭中11頭が15文字超だった。"""
    rows = parse_rows(_LIST_HTML)
    long_name = rows[1].name
    assert long_name == "Angel Numberの2024"
    assert len(long_name) > 15


def test_父母のセルに混ざるアイコンの角括弧を拾わない():
    """セル全体の get_text() だと 'Sea The Stars[]' になる。"""
    rows = parse_rows(_LIST_HTML)
    for row in rows:
        for value in (row.sire, row.broodmare, row.broodmare_sire, row.name):
            assert value is None or "[" not in value


def test_厩舎の東西接頭辞を落とす():
    rows = parse_rows(_LIST_HTML)
    assert rows[1].stable == "矢作芳人"
    assert rows[1].owner == "キャロットファーム"


def test_馬齢は実行年から求める():
    """移設元は `2026 - 生産年` と年を固定で書いており、翌年から別世代を取る。"""
    assert horse_age(2024, date(2026, 9, 8)) == 2
    assert horse_age(2024, date(2027, 1, 1)) == 3
    assert horse_age(2025, date(2027, 6, 1)) == 2


def test_見出しが変わったら行を返さない():
    """列位置の決め打ちではなく見出しで解決している（構造変化を 0 件として検出）。"""
    broken = _LIST_HTML.replace("馬名", "うまめい")
    assert parse_rows(broken) == []


def test_テーブルが無ければ空を返す():
    assert parse_rows("<html><body>該当データはありません</body></html>") == []


# --------------------------------------------------------------------------
# 世代ガード — 「別の世代を黙って取る」を止める
# --------------------------------------------------------------------------


class _NoWaitLimiter:
    def wait(self) -> None:  # pragma: no cover - 何もしない
        pass


def _patch_common(monkeypatch):
    from src.scrapers.netkeiba import pog_horse_list as mod

    monkeypatch.setattr(
        mod.ip_restriction, "require_not_restricted", lambda *a, **k: None
    )
    return mod


def test_要求と違う生年しか返らなければ中断する(monkeypatch):
    """`age_f` の意味が変われば、成功したまま別世代を取り込んでしまう。"""
    mod = _patch_common(monkeypatch)
    monkeypatch.setattr(mod, "fetch_page", lambda *a, **k: _LIST_HTML)

    result = mod.scrape(
        session=object(), birth_year=2023, today=date(2026, 9, 8),
        http=object(), limiter=_NoWaitLimiter(), dry_run=True,
    )

    assert result.aborted_reason is not None
    assert "生年" in result.aborted_reason
    assert result.saved == 0


def test_一致する行だけ取り込み食い違いを数える(monkeypatch):
    mod = _patch_common(monkeypatch)
    mixed = _LIST_HTML.replace(
        '<a href="https://db.netkeiba.com/horse/list.html?year=2024">2024</a>',
        '<a href="https://db.netkeiba.com/horse/list.html?year=2023">2023</a>',
        1,
    )
    monkeypatch.setattr(mod, "fetch_page", lambda *a, **k: mixed)

    result = mod.scrape(
        session=object(), birth_year=2024, today=date(2026, 9, 8),
        http=object(), limiter=_NoWaitLimiter(), to_page=1, dry_run=True,
    )

    assert result.aborted_reason is None
    assert result.parsed == 1
    assert result.birth_year_mismatch == 1


def test_総件数が読めなければ中断する(monkeypatch):
    """0 件を「対象なし」と誤解して success を返さない。"""
    mod = _patch_common(monkeypatch)
    monkeypatch.setattr(mod, "fetch_page", lambda *a, **k: "<html></html>")

    result = mod.scrape(
        session=object(), birth_year=2024, today=date(2026, 9, 8),
        http=object(), limiter=_NoWaitLimiter(), dry_run=True,
    )
    assert result.aborted_reason is not None
    assert "総件数" in result.aborted_reason


def test_未来の生産年は取りに行かない(monkeypatch):
    mod = _patch_common(monkeypatch)
    called = []
    monkeypatch.setattr(mod, "fetch_page", lambda *a, **k: called.append(1) or "")

    result = mod.scrape(
        session=object(), birth_year=2030, today=date(2026, 9, 8),
        http=object(), limiter=_NoWaitLimiter(), dry_run=True,
    )
    assert result.aborted_reason is not None
    assert called == []
