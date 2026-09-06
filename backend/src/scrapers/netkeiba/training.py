"""netkeiba の調教ページ（oikiri.html）の解析。**中央のみ**。

sekito `bin/scrape/netkeiba` の `scrape_training()` からの移設。

## 何を取っているか

`sekito.netkeiba.training` に入るのは「**評価語 + 半角スペース + ランク**」の
1 文字列（実測値: `キビキビ A` / `いま一息 D` / `まずまず C`）。
これは netkeiba 独自の追い切り短評で、**JRA-VAN の調教タイムでは代替できない**
（`keiba.wood_training` / `slope_training` はタイムであって評価ではない）。

## セレクタ

2026-09-06 に実ページを取って確認した並び:

    .Umaban           馬番
    .Training_Critic  評価語（"好調持続" など）
    .Rank_A / _B / _C / _D  ランク

移設元は位置で拾ったうえ「`Training_Critic` は JS で遅延描画される」として
`time.sleep(1)` を入れていたが、requests には JS が無く SSR 済みなので不要。
セレクタが意味を持っているので位置に頼る必要もない。
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ランクは class 名に埋まっている（Rank_A 〜 Rank_D）。
_RANK_PREFIX = "Rank_"


def parse(page: str) -> list[dict]:
    """調教ページを 1 頭 1 行のレコードへ。

    Returns:
        `horse_no` と `training` を持つレコード。評価語もランクも無い馬は落とす
        （未発表の馬が混じるため、空文字で上書きしない）。
    """
    soup = BeautifulSoup(page, "html.parser")

    records: list[dict] = []
    for row in soup.find_all("tr"):
        umaban = row.select_one(".Umaban")
        critic = row.select_one(".Training_Critic")
        if umaban is None or critic is None:
            continue

        horse_no = umaban.get_text(strip=True)
        if not horse_no.isdigit():
            continue

        評価 = critic.get_text(strip=True)
        rank = ""
        for cell in row.find_all(["td", "th"]):
            for cls in cell.get("class") or []:
                if cls.startswith(_RANK_PREFIX):
                    rank = cell.get_text(strip=True)
                    break
            if rank:
                break

        training = f"{評価} {rank}".strip() if (評価 or rank) else ""
        if not training:
            continue

        records.append({"horse_no": int(horse_no), "training": training})

    if not records:
        logger.debug("調教データが見つかりませんでした（未公開の可能性）")
    return records
