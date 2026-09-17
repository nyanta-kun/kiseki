"""netkeiba の「走行データ」ページ（ai_laptime.html）の解析。**中央の平地のみ**。

提供は DERBY ROOM。1 ページに3つのタブ（個別ラップ / 走行距離 / 走行解析）が
すべて入っているので、**1 レース 1 リクエストで全部取れる**。

## 🔴 マスターコース契約が無いと1着馬しか返らない

サーバは契約に応じて**返す馬を絞る**。SuperPremium では `horse_laptime` に
1着馬が1頭だけ入る（2026-09-15 / 09-16 実測・計 39 レース）。
ページ内の

    document.documentElement.classList.toggle('is-master', '0' === '1')

の左辺が `'1'` ならマスターコース。`RunningData.is_master` がこれを返す。
**`is_master=False` のデータを「全頭ぶん」として保存してはいけない**
（1着馬しか居ない＝勝った馬だけの偏った標本になる）。

## 公開時刻

レース翌週の金曜18時頃。それ以前は表が空で
「レース翌週の金曜日18時頃に公開予定です。」が出る（`RunningData.published`）。

🔴 学習に使うときは**公開時刻より前の情報として使わない**こと。レース当日には
存在しないデータなので、公開時刻を無視して過去走に付けると train/serve skew になる。

## 収録範囲（ページの注記・2026-09-16 実測）

    個別ラップ            2023年の重賞 + 2024年以降の全レース
    走行距離・位置        2025年以降
    完歩ピッチ・ストライド  2024年以降の重賞のみ

未収録のタブは**値がゼロで返る**ことがある（`horse_pitch` が全区間 `"0.000"`）。
ゼロ埋めをそのまま数値として保存すると「ピッチ 0 秒」という嘘になるので、
全区間ゼロの系列は `None` として落とす。

## ページの構造（2026-09-16 実測）

    <script> const race_rap = $.parseJSON('[[2600,"13.1"],...]');        … スタート側から並ぶ
             const horse_laptime = $.parseJSON('[{"Wakuban":5,"Umaban":7,"laptime":[...]}]');
             const horse_pitch / horse_stride = $.parseJSON('[{...,"PitchCount":[...]}]');
    <table id="milage_summary">
      tbody tr → td[0]=着順 td[1]=馬番 td[2]=馬名 td[3]=走行距離 td[4]=走破タイム
                 td[5]=距離補正走破タイム、以降 ハロンごとに (走行距離, 位置) の対

`race_rap` の各要素は `[ゴールまでの残り距離, "区間タイム"]` で、**先頭がスタート側**。
`lap_form.parse_lap_times`（JRA-VAN の `lap_times`）と同じ並びであることを
2026-09-06 の 35 レースで確認済み（全レース一致）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

__all__ = [
    "HorseRunningData",
    "RunningData",
    "is_published",
    "parse",
    "publication_at",
]

JST = ZoneInfo("Asia/Tokyo")

# 公開時刻。ページの注記は「レース翌週の金曜日18時頃」
_PUBLICATION_WEEKDAY = 4      # 月=0 … 金=4
_PUBLICATION_TIME = time(18, 0)

# `is-master` の判定。左辺が '1' ならマスターコース
_IS_MASTER_RE = re.compile(r"toggle\(\s*'is-master'\s*,\s*'(\d)'\s*===\s*'1'\s*\)")
# 未公開の注記
_NOT_PUBLISHED = "公開予定です"
# 埋め込み JSON
_JSON_RE_TMPL = r"const\s+{name}\s*=\s*\$\.parseJSON\('(.*?)'\)\s*;"


def publication_at(race_date: date_type) -> datetime:
    """走行データが公開される時刻（JST）＝**レース後で最初に来る金曜の18時**。

    実測（2026-09-15 時点）: 9/6(土) のレースは公開済み（最初の金曜 = 9/11）、
    9/13(土) のレースは未公開（最初の金曜 = 9/18）。

    🔴 学習で過去走に付けるときは、この時刻を過ぎている走だけを使うこと。
    レース当日には存在しないデータなので、無視すると train/serve skew になる。
    「18時頃」と幅のある告知なので、**遅らせる側に倒している**（早く使いすぎない）。
    """
    days = (_PUBLICATION_WEEKDAY - race_date.weekday()) % 7 or 7
    return datetime.combine(race_date + timedelta(days=days), _PUBLICATION_TIME, tzinfo=JST)


def is_published(race_date: date_type, now: datetime | None = None) -> bool:
    """`race_date` のレースの走行データが、`now`（既定は現在・JST）で公開済みか。"""
    current = now.astimezone(JST) if now else datetime.now(JST)
    return current >= publication_at(race_date)


def _embedded_json(page: str, name: str) -> object | None:
    """`const <name> = $.parseJSON('...')` の中身を取り出す。"""
    m = re.search(_JSON_RE_TMPL.format(name=re.escape(name)), page, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        logger.warning("netkeiba 走行データ: %s の JSON を読めなかった", name)
        return None


def _series(entries: object) -> list[float] | None:
    """`[[距離, "12.3"], ...]` を秒（または距離）の列にする。

    全要素ゼロの系列は未収録なので `None`（🔴 0 を値として保存しない）。
    """
    if not isinstance(entries, list) or not entries:
        return None
    out: list[float] = []
    for e in entries:
        if not isinstance(e, list) or len(e) < 2:
            return None
        try:
            out.append(float(e[1]))
        except (TypeError, ValueError):
            return None
    if all(v == 0.0 for v in out):
        return None
    return out


def _num(text: str) -> float | None:
    """`2,607.1m` → 2607.1 / `--` → None。"""
    s = text.replace(",", "").replace("m", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _time(text: str) -> float | None:
    """`2:40.7` → 160.7 秒 / `40.7` → 40.7 / それ以外は None。"""
    s = text.strip()
    if not s:
        return None
    try:
        if ":" in s:
            mm, ss = s.split(":", 1)
            return int(mm) * 60 + float(ss)
        return float(s)
    except ValueError:
        return None


@dataclass
class HorseRunningData:
    """1 頭ぶんの走行データ。取れなかった項目は `None`。

    Attributes:
        horse_number: 馬番。
        frame_number: 枠番。
        laps: 200m ごとの区間タイム（秒・スタート側から）。
        furlong_distances: 200m ごとに実際に走った距離（m・スタート側から）。
        positions: 200m ごとの内外の位置（内から 1・ラチ内側は負になりうる）。
        running_distance: 走行距離の合計（m）。
        finish_time: 走破タイム（秒）。
        corrected_time: 距離補正走破タイム（秒）。
        pitch: 200m ごとの完歩ピッチ（秒/完歩）。
        stride: 200m ごとのストライド長（m）。
    """

    horse_number: int
    frame_number: int | None = None
    laps: list[float] | None = None
    furlong_distances: list[float] | None = None
    positions: list[float | None] | None = None
    running_distance: float | None = None
    finish_time: float | None = None
    corrected_time: float | None = None
    pitch: list[float] | None = None
    stride: list[float] | None = None


@dataclass
class RunningData:
    """1 レースぶんの走行データ。

    Attributes:
        is_master: マスターコース契約で見えているか。🔴 False なら1着馬しか入らない。
        published: 走行データが公開済みか（翌週金曜18時頃）。
        race_laps: レースのラップ（秒・スタート側から）。JRA-VAN の `lap_times` と同じ並び。
        horses: 馬番 → 走行データ。
    """

    is_master: bool
    published: bool
    race_laps: list[float] | None = None
    horses: dict[int, HorseRunningData] = field(default_factory=dict)

    @property
    def n_horses(self) -> int:
        return len(self.horses)


def _merge_series(horses: dict[int, HorseRunningData], entries: object,
                  key: str, attr: str) -> None:
    """`horse_laptime` 系の JSON を馬ごとに取り込む。"""
    if not isinstance(entries, list):
        return
    for e in entries:
        if not isinstance(e, dict) or "Umaban" not in e:
            continue
        try:
            umaban = int(e["Umaban"])
        except (TypeError, ValueError):
            continue
        horse = horses.setdefault(umaban, HorseRunningData(horse_number=umaban))
        if horse.frame_number is None:
            try:
                horse.frame_number = int(e["Wakuban"])
            except (KeyError, TypeError, ValueError):
                pass
        setattr(horse, attr, _series(e.get(key)))


def _parse_milage_table(soup: BeautifulSoup, horses: dict[int, HorseRunningData]) -> None:
    """走行距離の表（`#milage_summary`）から距離・タイム・位置を取り込む。"""
    table = soup.find("table", id="milage_summary")
    if not isinstance(table, Tag):
        return
    body = table.find("tbody")
    if not isinstance(body, Tag):
        return
    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 6:
            continue
        try:
            umaban = int(cells[1].get_text(strip=True))
        except ValueError:
            continue
        horse = horses.setdefault(umaban, HorseRunningData(horse_number=umaban))
        horse.running_distance = _num(cells[3].get_text(strip=True))
        horse.finish_time = _time(cells[4].get_text(strip=True))
        horse.corrected_time = _time(cells[5].get_text(strip=True))
        dists: list[float] = []
        positions: list[float | None] = []
        for td in cells[6:]:
            classes: list[str] = list(td.get("class") or [])
            if "PositionCell" in classes:
                pos = td.get("data-position")
                # 属性が無い・複数値のときは表示文字列から読む
                text = pos if isinstance(pos, str) else td.get_text(strip=True)
                positions.append(_num(text))
            else:
                v = _num(td.get_text(strip=True))
                if v is not None:
                    dists.append(v)
        horse.furlong_distances = dists or None
        horse.positions = positions or None


def parse(page: str) -> RunningData:
    """走行データページを解析する。

    🔴 例外を投げずに「取れなかった」を表す。呼び出し側は `is_master` /
    `published` / `n_horses` を見て保存するかどうかを決めること。
    """
    m = _IS_MASTER_RE.search(page)
    is_master = bool(m and m.group(1) == "1")
    soup = BeautifulSoup(page, "html.parser")

    horses: dict[int, HorseRunningData] = {}
    _merge_series(horses, _embedded_json(page, "horse_laptime"), "laptime", "laps")
    _merge_series(horses, _embedded_json(page, "horse_pitch"), "PitchCount", "pitch")
    _merge_series(horses, _embedded_json(page, "horse_stride"), "stride", "stride")
    _parse_milage_table(soup, horses)

    data = RunningData(
        is_master=is_master,
        published=_NOT_PUBLISHED not in soup.get_text(" ", strip=True) or bool(horses),
        race_laps=_series(_embedded_json(page, "race_rap")),
        horses=horses,
    )
    if not is_master and data.n_horses > 1:
        # 契約判定と実データが食い違ったら、判定側の正規表現が変わった可能性がある
        logger.warning("netkeiba 走行データ: is-master=0 なのに %d 頭ぶん入っている",
                       data.n_horses)
    return data
