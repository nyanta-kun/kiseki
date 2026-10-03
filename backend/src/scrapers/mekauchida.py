"""外部サイト「メカウチダ」（仮想購入サイト）の今日ページのパーサ。

対象: ``https://d374d8pjjxnppk.cloudfront.net/index.html``（中央）/ ``nar/index.html``（地方）
（サイトの性質・更新タイミングの実測は ``docs/jra_ext_virtualbet_feature_plan_2026_09_27.md``）

各レースのカード（``<div class='tl {decided|wait|missed} [has]'>``）から、
発走時刻・場名・R と、買い目の馬（``<div class='horse'>``）を取り出す。

買い目は 2 種類ある:

| kind | HTML | 意味 |
|---|---|---|
| ``buy`` | ``<span class='res buy'>``（精算後は ``hit`` 等） | 判断済み（発走 3 分前の正式判断） |
| ``pv``  | ``<div class='horse is-pv'>`` / ``<span class='res pv'>`` | 見込み（朝 09:00、発走 30 分前からはその時点のオッズで仮判断） |

🔴 **構造が変わったら 0 件で黙らずに例外を出す。** 「今日は推奨なし」と
「読めていない」が区別できなくなるため。日付が今日でない場合も例外にする
（サイトが更新されていない日に前日の買い目を通知しないため）。

DB にも HTTP にも依存しない純関数。
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field

SITE_URL = "https://d374d8pjjxnppk.cloudfront.net/index.html"
NAR_SITE_URL = "https://d374d8pjjxnppk.cloudfront.net/nar/index.html"
"""地方版。ページ構造は中央と同じ（2026-10-03 確認）。"""

_RACE_CARD_RE = re.compile(r"<div class='tl ([a-z ]+)'>")
_HERO_DATE_RE = re.compile(r"<div class='next' data-date='(\d{4}-\d{2}-\d{2})'")
_HERO_TITLE_RE = re.compile(r"<div class='hd'>(\d+)<small>月</small>(\d+)<small>")
_UPDATED_RE = re.compile(r"<div class=[\"']upd[\"']>更新 ([0-9/]+ [0-9:]+)")
_RT_RE = re.compile(r"<span class='rt'>(\d{1,2}):(\d{2})</span>")
_RV_RE = re.compile(r"<span class='rv'>([^<]+)<b>(\d+)R</b></span>")
# 精算後は res buy が res hit / miss 等に変わる。pv 以外は判断済みとして扱う
_HORSE_RE = re.compile(r"<div class='horse( is-pv)?'>(.*?)<span class='res ([a-z]+)'>", re.S)
_NUM_RE = re.compile(r"<span class='num[^']*'[^>]*>(\d+)</span>")
_HN_RE = re.compile(r"<div class='hn'>([^<]*)((?:<span class='flag'>[^<]*</span>)*)</div>")
_FLAG_RE = re.compile(r"<span class='flag'>([^<]*)</span>")
_HS_RE = re.compile(r"<div class='hs'>(.*?)</div>", re.S)
_POP_RE = re.compile(r"(\d+)番人気")
_WIN_RE = re.compile(r"単 ([0-9.]+)")
# 中央は「複 2.0–2.7」、地方は「複 4.5〜」（上限なし）
_PLACE_RE = re.compile(r"複 ([0-9.]+)(?:[–\-〜~]([0-9.]+)?)?")
_EV_RE = re.compile(r"期待値 ([0-9.]+)")


class MekauchidaParseError(ValueError):
    """ページ構造が想定と違う（黙って 0 件にしないための例外）。"""


@dataclass(frozen=True)
class SitePick:
    """サイトの買い目 1 頭。"""

    horse_number: int
    horse_name: str
    kind: str  # "buy"（判断済み）/ "pv"（見込み）
    flags: tuple[str, ...]  # 買い方の札（"A" / "B"）。無いこともある
    popularity: int | None
    win_odds: float | None
    place_odds_low: float | None
    place_odds_high: float | None
    expected_value: float | None


@dataclass(frozen=True)
class SiteRace:
    """サイトのレースカード 1 枚。"""

    venue: str  # 場名（"東京" 等。keiba.races.course_name と同じ表記）
    race_number: int
    post_hhmm: str  # "1510"
    state: str  # "decided" / "wait" / "missed"
    picks: tuple[SitePick, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SitePage:
    """今日ページ全体。"""

    date: str  # "YYYYMMDD"
    updated: str | None  # サイト表記の「更新 10/03 15:00」
    races: tuple[SiteRace, ...]


def _to_float(m: re.Match[str] | None, group: int = 1) -> float | None:
    return float(m.group(group)) if m else None


def _parse_pick(is_pv: str | None, body: str, res: str) -> SitePick:
    num = _NUM_RE.search(body)
    hn = _HN_RE.search(body)
    if not num or not hn:
        raise MekauchidaParseError(f"買い目の馬番・馬名が読めない: {body[:200]!r}")
    # 見込みの馬は「09:00時点」の行と「今（HH:MM）」の行を持つ。値は最初の行（判断の根拠）を使う
    hs = _HS_RE.search(body)
    hs_text = html_lib.unescape(re.sub(r"<[^>]+>", " ", hs.group(1))) if hs else ""
    pop = _POP_RE.search(hs_text)
    place = _PLACE_RE.search(hs_text)
    kind = "pv" if (is_pv or res == "pv") else "buy"
    return SitePick(
        horse_number=int(num.group(1)),
        horse_name=html_lib.unescape(hn.group(1)).strip(),
        kind=kind,
        flags=tuple(_FLAG_RE.findall(hn.group(2))),
        popularity=int(pop.group(1)) if pop else None,
        win_odds=_to_float(_WIN_RE.search(hs_text)),
        place_odds_low=_to_float(place, 1),
        place_odds_high=float(place.group(2)) if place and place.group(2) else None,
        expected_value=_to_float(_EV_RE.search(hs_text)),
    )


def parse_today_page(html: str, year: int) -> SitePage:
    """今日ページの HTML を読む。

    Args:
        html: ``index.html`` の本文
        year: 年（「次の判断」が消えた後は見出しの月日しか無いため補う）

    Returns:
        ページ全体。レースは HTML の並び順（発走順）

    Raises:
        MekauchidaParseError: 日付が読めない・レースが 1 件も読めない・
            カードの見出しが読めない場合
    """
    date_m = _HERO_DATE_RE.search(html)
    if date_m:
        date = date_m.group(1).replace("-", "")
    else:
        # 「次の判断」は最終レースの判断後に消える。その場合は見出しの月日しか無い
        title_m = _HERO_TITLE_RE.search(html)
        if not title_m:
            raise MekauchidaParseError("ページの日付が読めない")
        date = f"{year:04d}{int(title_m.group(1)):02d}{int(title_m.group(2)):02d}"
    upd = _UPDATED_RE.search(html)

    starts = [(m.start(), m.group(1)) for m in _RACE_CARD_RE.finditer(html)]
    if not starts:
        raise MekauchidaParseError("レースカードが 1 件も無い（ページ構造が変わった可能性）")

    races: list[SiteRace] = []
    for i, (pos, cls) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(html)
        card = html[pos:end]
        rt = _RT_RE.search(card)
        rv = _RV_RE.search(card)
        if not rt or not rv:
            raise MekauchidaParseError(f"レースの発走時刻・場名が読めない: {card[:300]!r}")
        state = cls.split()[0]
        # 予想の詳細（<details>）より前だけを見る。詳細の表に馬番が大量にあるため
        head = card.split("<details", 1)[0]
        picks = tuple(_parse_pick(*m.groups()) for m in _HORSE_RE.finditer(head))
        if "has" in cls.split() and not picks:
            raise MekauchidaParseError(f"買い目ありのカードから馬が読めない: {head[:300]!r}")
        races.append(
            SiteRace(
                venue=html_lib.unescape(rv.group(1)).strip(),
                race_number=int(rv.group(2)),
                post_hhmm=f"{int(rt.group(1)):02d}{rt.group(2)}",
                state=state,
                picks=picks,
            )
        )
    return SitePage(date=date, updated=upd.group(1) if upd else None, races=tuple(races))
