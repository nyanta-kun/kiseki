"""netkeiba のタイム指数ページ（speed.html）の解析。

sekito `bin/scrape/netkeiba` の `scrape_time_index()`（661 行）からの移設。

## 移設で作り直したところ

移設元は「`pandas.read_html` で読む → その値は間違っているので BeautifulSoup で
読み直して上書きする」という二重構造で、中央と地方に別々の列マッピングを持っていた。
実ページを取って調べたところ（2026-09-06）、**その複雑さは要らない**:

    データ行の td の並びは中央・地方で同一
      [0]枠 [1]馬番 [2]印 [3]馬名 [4]性齢 [5]斤量 [6]騎手
      [7]最高 [8]５走平均 [9]距離 [10]コース [11]3走前 [12]2走前 [13]前走
      [14]単勝オッズ [15]人気

    ヘッダは 14 列（「近走成績」が colspan=3）なので、**ヘッダの位置で
    データ列を数えてはいけない**。移設元が pandas を使って苦労していた原因。

## 🔴 隠しスパンを外さないと値が壊れる

各セルには並べ替え用の隠し値と表示値が両方入っている:

    <td class="sk__max_index">
      <span class="Sort_Function_Data_Hidden">1081</span>
      <a href="...">81</a>
    </td>

`get_text()` をそのまま使うと **"108181"** になる。馬名も
"ルースソラールルースソラール" と重複する（隠しスパンと a タグの両方に入るため）。
移設元にあった「文字列を半分に割って前後が同じなら片方を捨てる」「スペース区切りの
最後を採る」といった後処理は、**この隠しスパンを外していなかったことの帳尻合わせ**。
先に外せば全部不要になる。

## 列の同定はマーカー優先・位置フォールバック

中央と地方で位置は同じだが、**マーカーの体系は違う**（2026-09-06 実測）:

    中央: td の class  sk__max_index / sk__average_index /
                       sk__max_distance_index / sk__max_course_index /
                       sk__index3 / sk__index2 / sk__index1
    地方: td の class  Speed_List03 / 04 / 05 / 06 / 09 / 10 / 11
          a の id      racelink_max_index_N など

マーカーで引けたらそれを使い、無ければ位置に落ちて **WARNING を出す**。
netkeiba が列を入れ替えたとき、位置決め打ちだと「別の指数が別の列に静かに入る」
という最悪の壊れ方をする。警告が出れば気づける。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

# 並べ替え用の隠し値。表示値を取る前に必ず取り除く。
SORT_KEY_CLASS = "Sort_Function_Data_Hidden"

# フィールド → (データ行での位置, 中央のクラス, 地方のクラス)
# 位置は中央・地方で同一。マーカーだけ体系が違う（地方は馬番・馬名にマーカーが無い）。
_FIELDS: tuple[tuple[str, int, str | None, str | None], ...] = (
    ("horse_no", 1, "sk__umaban", None),
    ("horse_name", 3, "sk__horse_name", None),
    ("idx_max", 7, "sk__max_index", "Speed_List03"),
    ("idx_ave", 8, "sk__average_index", "Speed_List04"),
    ("idx_distance", 9, "sk__max_distance_index", "Speed_List05"),
    ("idx_course", 10, "sk__max_course_index", "Speed_List06"),
    ("idx_third", 11, "sk__index3", "Speed_List09"),
    ("idx_second", 12, "sk__index2", "Speed_List10"),
    ("idx_last", 13, "sk__index1", "Speed_List11"),
)

# 中央のマーカーが 1 つでもあれば中央版のページとみなす。
_JRA_MARKER_PREFIX = "sk__"

# タイム指数が無いことを示す文言。中央と地方で出方が違う。
UNAVAILABLE_MESSAGES = (
    "タイム指数はございません",
    "データがありません",
    "タイム指数データがありません",
)


def cell_text(cell: Tag) -> str:
    """セルの**表示値**を返す。並べ替え用の隠しスパンを取り除く。

    🔴 これを外さないと "1081" + "81" が連結されて "108181" になる。
    """
    clone = BeautifulSoup(str(cell), "html.parser")
    for hidden in clone.select(f".{SORT_KEY_CLASS}"):
        hidden.decompose()
    return clone.get_text(strip=True)


def classify_unavailability(page: str, race_start: datetime | None) -> str:
    """タイム指数が無いとき、一時的な未公開か恒久的な不在かを分ける。

    区別する理由は再試行間隔が違うこと（`not_yet_published` は 1 時間、
    `not_available` は 6 時間）。移設元の判定をそのまま持ってくる:

        レースまで 1 時間以上ある       → まだ出ていないだけ
        レース終了から 24 時間以上経過   → もう出ない
        その間                          → 一時的な未公開とみなす（安全側）

    Args:
        page: ページ本文。
        race_start: 発走時刻。None なら安全側（未公開）に倒す。

    Returns:
        "not_yet_published" か "not_available"。
    """
    if not any(msg in page for msg in UNAVAILABLE_MESSAGES):
        # そもそも「無い」と書いていない。呼び出し側が別に判断する。
        return "not_available"

    if race_start is None:
        logger.debug("レース時刻不明のため未公開と判定（安全側）")
        return "not_yet_published"

    start = race_start.replace(tzinfo=JST) if race_start.tzinfo is None else race_start.astimezone(JST)
    until_race = start - datetime.now(JST)

    if until_race > timedelta(hours=1):
        return "not_yet_published"
    if until_race < timedelta(hours=-24):
        return "not_available"
    return "not_yet_published"


def _find_speed_table(soup: BeautifulSoup) -> Tag | None:
    """タイム指数表を探す。ヘッダに「最高」と「５走平均」があるものを採る。"""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        joined = " ".join(headers)
        if "最高" in joined and ("平均" in joined or "５走" in joined):
            return table
    return None


def _is_jra_page(cells: list[Tag]) -> bool:
    """中央版のページか。中央だけ `sk__*` クラスが付く（2026-09-06 実測）。"""
    return any(
        any(c.startswith(_JRA_MARKER_PREFIX) for c in (cell.get("class") or []))
        for cell in cells
    )


def _column_index(cells: list[Tag], field: str, position: int,
                  marker: str | None) -> int | None:
    """フィールドに対応する td の位置を決める。

    マーカーで引けたらそれを使う。**マーカーがある建前なのに見つからないときだけ**
    WARNING を出して位置に落ちる。netkeiba が列を入れ替えたとき、位置決め打ちだと
    「別の指数が別の列に静かに入る」という最悪の壊れ方をするので、気づけるようにする。

    地方の馬番・馬名のように、そもそもマーカーが無いフィールドは黙って位置を使う。
    """
    if marker:
        for i, cell in enumerate(cells):
            if marker in (cell.get("class") or []):
                return i
        logger.warning(
            "%s のマーカー(%s)が見つかりません。位置 %d に落とします "
            "— netkeiba の列構成が変わった可能性があります",
            field, marker, position,
        )
    return position if position < len(cells) else None


def parse(page: str) -> list[dict]:
    """タイム指数ページを 1 頭 1 行のレコードへ。

    馬番が整数として読めない行（注記など）は捨てる。
    """
    soup = BeautifulSoup(page, "html.parser")
    table = _find_speed_table(soup)
    if table is None:
        return []

    records: list[dict] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue  # ヘッダ行（th のみ）

        is_jra = _is_jra_page(cells)
        values: dict[str, str] = {}
        for field, position, jra_class, nar_class in _FIELDS:
            marker = jra_class if is_jra else nar_class
            i = _column_index(cells, field, position, marker)
            values[field] = cell_text(cells[i]) if i is not None else ""

        if not values["horse_no"].isdigit():
            continue

        records.append({
            "horse_no": int(values["horse_no"]),
            "horse_name": values["horse_name"],
            **{k: (values[k] or None) for k in
               ("idx_max", "idx_ave", "idx_distance", "idx_course",
                "idx_third", "idx_second", "idx_last")},
        })
    return records
