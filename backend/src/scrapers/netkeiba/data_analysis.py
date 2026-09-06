"""netkeiba のデータ分析ページ（data_top.html）の解析。

sekito `bin/scrape/netkeiba` の `scrape_data_analysis()` からの移設。
書き込み先は `sekito.netkeiba` ではなく `sekito.netkeiba_data_analysis`
（レース単位の 1 行 + JSONB）。**sekito のレース詳細 UI が使う**ので、
netkeiba の取得対象から外せない。

取るもの:
    top_horse_1..3   ページ上部「データ上位馬」の馬番（最大 3 頭）
    analysis_data    「出走馬分析」の各見出しと該当馬番（JSONB）

見出しの分類は移設元の対応をそのまま持つ。netkeiba 側の文言が変わると
**該当キーが黙って落ちる**ので、未知の見出しは WARNING に出す
（移設元は黙って捨てていた）。
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# データが無いことを示す文言。
UNAVAILABLE_MESSAGES = ("データがありません", "出走データがありません")

MAX_TOP_HORSES = 3
MAX_HORSE_NO = 18

# 「出走馬分析」の見出し → JSONB のキー。
# 見出しは「〜が得意な馬」「〜で実績がある馬」の形。含まれる語で判定する。
_TITLE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("コース", "得意"), "course_suited"),
    (("距離", "得意"), "distance_suited"),
    (("競馬場", "得意"), "racecourse_suited"),
    (("馬場状態", "得意"), "track_condition_suited"),
    (("レース間隔", "実績"), "race_interval_suited"),
    (("調教評価", "実績"), "training_suited"),
    (("厩舎コメント評価", "実績"), "stable_comment_suited"),
    # 🔴 2026-09-06 に移植の過程で見つけた取りこぼし。
    #    netkeiba は「今回のクッション値が得意な馬」を出しているが、移設元の
    #    分類に無いため**黙って捨てられていた**（本番 `netkeiba_data_analysis`
    #    の直近30日に現れるキーは 7 種類だけで、クッション値は 1 件も無い）。
    #    未知の見出しを WARNING に出すようにしたのはこれを拾うため。
    (("クッション値", "得意"), "cushion_suited"),
)


def _horse_numbers(container) -> list[int]:
    """コンテナ内の馬番を重複なしで拾う。"""
    numbers: list[int] = []
    for span in container.find_all("span", class_="Umaban_Num"):
        text = span.get_text(strip=True)
        if text.isdigit() and 1 <= int(text) <= MAX_HORSE_NO:
            n = int(text)
            if n not in numbers:
                numbers.append(n)
    return numbers


def parse(page: str) -> dict | None:
    """データ分析ページを解析する。

    Returns:
        `top_horses` と `analysis_data` を持つ dict。データが無ければ None。
    """
    if any(msg in page for msg in UNAVAILABLE_MESSAGES):
        return None

    soup = BeautifulSoup(page, "html.parser")

    # ページ上部の「データ上位馬」。出走馬分析セクションより前に出る前提。
    top_horses = _horse_numbers(soup)[:MAX_TOP_HORSES]

    analysis: dict[str, dict] = {}
    pickup = soup.find("div", id="RaceDataPickup01")
    if pickup:
        for table in pickup.find_all("table", class_="PickupRaceDataTable01"):
            title_div = table.find("div", class_="PickupHorseTableTitle")
            if not title_div:
                continue
            title = title_div.get_text(strip=True)

            key = next(
                (k for words, k in _TITLE_RULES if all(w in title for w in words)),
                None,
            )
            if key is None:
                # 移設元は黙って捨てていた。文言が変わると項目が静かに消えるので出す。
                logger.warning("未知の出走馬分析の見出しです（取りこぼしています）: %r", title)
                continue

            analysis[key] = {"horses": _horse_numbers(table), "description": title}

    if not top_horses and not analysis:
        return None
    return {"top_horses": top_horses, "analysis_data": analysis}
