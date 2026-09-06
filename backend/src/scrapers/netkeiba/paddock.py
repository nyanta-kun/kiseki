"""netkeiba のパドックページ（paddock.html）の解析。**中央のみ**。

sekito `bin/scrape/netkeiba-paddock` からの移設。

## 🔴 この移設で直る不具合

移設元は L154 で `r.content.decode('euc-jp', errors='replace')` と決め打ちしていた。
**paddock.html は UTF-8**（2026-09-06 実測: `Content-Type: text/html; charset=UTF-8`）
なので必ず化ける。しかも UPSERT が `horse_name = EXCLUDED.horse_name` だったため、
朝の `netkeiba-index` が書いた**正しい馬名を上書きして壊していた**:

    2026-09-06 実測（当日の中央）
      パドック取得あり  192 行 → 192 行が化け（100%）
      パドック取得なし  299 行 → 0 行（0%）

sekito@827cdb3 の文字コード修正は `bin/scrape/netkeiba` **1 ファイルだけ**に入って
おり、こちらには届いていなかった（[[paddock-mojibake]]）。移植版は `decode_page` を使う。

⚠️ **評価も化けていた。** `p_rank` は A/B/C のほかに `穴` があり、これは非 ASCII:

    '穴'.encode('utf-8').decode('euc-jp', errors='replace') == '腥�'

kiseki のスコア表は `("特注", "穴"): 60.0` なので、化けた行はキーに一致せず
**中立 50.0 に潰れていた**（実測 26 行・2026-08-22 以降の全期間）。

## ページの構造（2026-09-06 実測）

    table.Paddock_Table       1 つ目が「人気」、2 つ目以降が「特注」
      td.Waku                 馬番
      td.Horse_Name           馬名
      td.Hyoka                評価（A / B / C / 穴）
      td.Comment              寸評

`p_type`（人気 / 特注）はテーブルの順番から決まる Python 側のリテラルで、
ページから取る値ではない。だから化けていなかった。
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# テーブルの並び順 → p_type。移設元と同じ規則。
_TABLE_TYPES = ("人気", "特注")

# 評価として妥当な値。これ以外が来たら解析かページが変わっている。
VALID_RANKS = ("A", "B", "C", "穴")


def parse(page: str) -> list[dict]:
    """パドックページを 1 頭 1 行のレコードへ。

    Returns:
        `horse_no` / `horse_name` / `p_rank` / `p_comment` / `p_type` を持つレコード。
    """
    soup = BeautifulSoup(page, "html.parser")
    tables = soup.select("table.Paddock_Table")
    if not tables:
        return []

    records: list[dict] = []
    for i, table in enumerate(tables):
        # 3 つ目以降が現れたら「特注」に寄せる（移設元と同じ）。
        p_type = _TABLE_TYPES[min(i, len(_TABLE_TYPES) - 1)]

        for row in table.find_all("tr"):
            umaban = row.select_one("td.Waku")
            if umaban is None:
                continue
            horse_no = umaban.get_text(strip=True)
            if not horse_no.isdigit():
                continue

            name_el = row.select_one("td.Horse_Name")
            hyoka_el = row.select_one("td.Hyoka")
            comment_el = row.select_one("td.Comment")

            p_rank = hyoka_el.get_text(strip=True) if hyoka_el else ""
            if p_rank and p_rank not in VALID_RANKS:
                # 想定外の評価。文字コードや構造の変化を疑う手掛かりとして出す。
                logger.warning("想定外のパドック評価です: %r (p_type=%s 馬番=%s)",
                               p_rank, p_type, horse_no)

            records.append({
                "horse_no": int(horse_no),
                "horse_name": name_el.get_text(strip=True) if name_el else "",
                "p_rank": p_rank,
                "p_comment": comment_el.get_text(strip=True) if comment_el else "",
                "p_type": p_type,
            })

    return records
