"""netkeiba の調教ページ（oikiri.html）の解析。**中央のみ**。

sekito `bin/scrape/netkeiba` の `scrape_training()` からの移設。

## 何を取っているか

`sekito.netkeiba.training` に入るのは「**評価語 + 半角スペース + ランク**」の
1 文字列（実測値: `キビキビ A` / `いま一息 D` / `まずまず C`）。
これは netkeiba 独自の追い切り短評で、**JRA-VAN の調教タイムでは代替できない**
（`keiba.wood_training` / `slope_training` はタイムであって評価ではない）。

## 🔴 レイアウトが 2 種類ある（行で区切ってはいけない）

2026-09-07 に埋め戻しで 158 レースが空振りして分かった。同じ oikiri.html に
**1 頭 1 行**と**1 頭 2 行**の 2 つの並びがある:

    1 頭 1 行（2026-09-06 阪神1R で確認）
      Waku / Umaban / 印 / 馬名 / 日付 / … / TrainingLoad / Training_Critic / Rank_X

    1 頭 2 行（2026-08-02 中京7R で確認）
      row A: Waku / Umaban / 印 / 馬名 / TrainingReview_Cell（追い切り寸評）
      row B: 日付 / コース / 馬場 / 乗り役 / タイム / 位置 / TrainingLoad /
             Training_Critic / Rank_X          ← **Umaban が無い**

「同じ `<tr>` に馬番と評価がある」前提だと後者で必ず 0 件になる。ページには
データがあるのに `not_available` を記録してしまう——例外も出ない静かな取りこぼし。

そこで**行を無視して、表のセルを文書順に走査する**。`.Umaban` が出たらそこから
次の `.Umaban` までを 1 頭ぶんとみなす。どちらの並びでも同じ結果になる。

## セレクタ

    .Umaban           馬番
    .Training_Critic  評価語（"好調持続" など）
    .Rank_A .. Rank_D ランク

移設元は位置で拾ったうえ「Training_Critic は JS で遅延描画される」として
`time.sleep(1)` を入れていたが、requests に JS は無く SSR 済みなので不要。
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# ランクは class 名に埋まっている（Rank_A 〜 Rank_D）。
_RANK_PREFIX = "Rank_"


def _classes(cell: Tag) -> list[str]:
    """タグの class を必ずリストで返す。

    bs4 の `Tag.get()` は多値属性のために `str | AttributeValueList | None` を
    返す。単一値のときに文字列で来ると `in` 判定が部分一致になって誤爆する。
    """
    value = cell.get("class")
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def parse(page: str) -> list[dict]:
    """調教ページを 1 頭 1 行のレコードへ。

    行ではなくセルの文書順で区切る（レイアウトが 2 種類あるため。上の docstring 参照）。

    Returns:
        `horse_no` と `training`（"評価語 ランク"）を持つレコード。
        評価語もランクも無い馬は落とす（未発表の馬を空文字で上書きしないため）。
    """
    soup = BeautifulSoup(page, "html.parser")

    horses: list[dict] = []
    current: dict | None = None

    for cell in soup.find_all(["td", "th"]):
        classes = _classes(cell)

        if "Umaban" in classes:
            text = cell.get_text(strip=True)
            current = {"horse_no": int(text), "critic": "", "rank": ""} if text.isdigit() else None
            if current is not None:
                horses.append(current)
            continue

        if current is None:
            continue

        if "Training_Critic" in classes:
            current["critic"] = cell.get_text(strip=True)
        else:
            for cls in classes:
                if cls.startswith(_RANK_PREFIX):
                    current["rank"] = cell.get_text(strip=True)
                    break

    records = []
    for horse in horses:
        training = f"{horse['critic']} {horse['rank']}".strip()
        if training:
            records.append({"horse_no": horse["horse_no"], "training": training})

    if not records:
        logger.debug("調教データが見つかりませんでした（未公開の可能性）")
    return records
