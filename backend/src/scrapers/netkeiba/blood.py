"""netkeiba の血統・バイアスページ（bias.html）の解析。

sekito `bin/scrape/netkeiba` の `scrape_blood()` からの移設。

⚠️ **日次では取らない。埋め戻し専用。**
    2026-09-06 に取得対象から外した。JRA-VAN が上位互換で、実測（中央 522 頭）で
    netkeiba 522 / JRA-VAN 522・値の一致 99.6%（不一致は「カラヴァッジオ」と
    "Caravaggio" の表記差だけ）だった。地方は JRA-VAN の充足が 81〜87% で、
    残りは中央登録歴の無い地方専用馬。
    その地方専用馬ぶんだけは netkeiba にしか無いので、埋め戻しに使う。

## ページの構造（2026-09-06 実測）

    table.Bias
      td.UmaBan       馬番
      td.Horse_Name   馬名
      td.Blood_Cell   父 → 母父 の順に 2 つ

`Blood_Cell` が 2 つ並ぶだけで、どちらが父かはクラスで区別できない。
**文書順で 1 つ目が父・2 つ目が母父**（ヘッダも「父名」「母父名」の順）。
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 血統セルの並び。ヘッダは「父名」「母父名」の順。
_BLOOD_FIELDS = ("sire", "broodmare_sire")


def parse(page: str) -> list[dict]:
    """血統ページを 1 頭 1 行のレコードへ。

    Returns:
        `horse_no` / `sire` / `broodmare_sire` を持つレコード。
        血統セルが揃わない行（見出しや注記）は落とす。
    """
    soup = BeautifulSoup(page, "html.parser")
    table = soup.select_one("table.Bias")
    if table is None:
        return []

    records: list[dict] = []
    for row in table.find_all("tr"):
        umaban = row.select_one("td.UmaBan")
        if umaban is None:
            continue
        horse_no = umaban.get_text(strip=True)
        if not horse_no.isdigit():
            continue

        cells = row.select("td.Blood_Cell")
        if len(cells) < len(_BLOOD_FIELDS):
            logger.debug("血統セルが足りません（馬番 %s・%d 個）", horse_no, len(cells))
            continue

        record: dict[str, object] = {"horse_no": int(horse_no)}
        for field, cell in zip(_BLOOD_FIELDS, cells):
            record[field] = cell.get_text(strip=True)
        records.append(record)

    return records
