"""POG 指名候補（2歳馬）一覧の取得。sekito `bin/scrape/netkeiba-horses-bulk` の移設先。

POG はデビュー前の2歳馬を指名するので、JV-Link にまだ載らない馬の一覧が要る。
`keiba.horses` は SE レコード（出走）由来なので**出走した馬しか居ない**
（2026-09-08 実測: 2024年産は netkeiba 7,943頭に対し `keiba.horses` は 1,235頭。
2026年の指名70頭のうち 36頭しか居ない）。だから外部の一覧が必要になる。

## 🔴 移設元の取得経路は既に死んでいる

sekito 版は `GET https://db.netkeiba.com/?pid=horse_list&under_age=N&over_age=N` を
叩いていたが、2026-09-08 実測で **97バイトのスタブ**（`URL: /?pid=horse_list...` と
だけ書かれた赤字）しか返らない。ログイン済みセッションでも同じ。
netkeiba の馬検索は path ベース + JS 描画へ移っている。

    GET  /?pid=horse_list ...           97 バイト（スタブ）
    POST /                              DB トップページが返る
    GET  /horse/search_all.html         JS 描画の枠だけ
    GET  /horse/list.html?age_f=2&...   **これが現行**（388KB・7,943件・100件/ページ）

そのまま移植していたら「成功したのに 0 件」を作るところだった。

## 現行の契約（`/horse/search_detail.html` のフォームから読んだもの）

    GET https://db.netkeiba.com/horse/list.html
      range=all  word=  match=p
      age_f / age_t     ← 馬齢。生年ではない
      sort=name-asc     limit=20|50|100    page=N

`limit=100` が使えるので、移設元（20件/ページ）の 1/5 のリクエスト数で済む。

## 🔴 馬齢と生年を取り違えない

移設元は `age = 2026 - birth_year` と**年を固定で書いていた**。翌年になると
黙って1つ違う世代を取る。ここでは実行日（JST）から求め、さらに
**取得した行の「生年」列が要求した生産年と一致することを確認**する。
一致しない行は捨て、ページ全体が食い違えば中断する。

## 🔴 netkeiba の馬ID は数字10桁とは限らない

外国産馬は `000a02d612` のような英数字になる。**先頭4桁を生産年として使えない。**
生年は必ず「生年」列から取る。

## 移設で直したもの

- 馬名を `name[:15]` と切り詰めていた（`keiba.pog_horses.name` は 100 文字）
- `birthday` に `{生産年}-01-01` を**捏造**していた（一覧に生年月日は無い）。
  実際 sekito 側は 2024年産 7,855頭すべてが 01-01 になっている。ここでは入れない
- 父・母・母父のセルには詳細リンクのアイコン（`[ ]`）が同居する。セル全体の
  テキストを取ると `Palace Pier[]` になるので、**最初の `<a>` の title/文字列**を使う
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date

import requests
from bs4 import BeautifulSoup, Tag
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import ip_restriction
from .decode import decode_page
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

LIST_URL = "https://db.netkeiba.com/horse/list.html"
SEARCH_PAGE = "https://db.netkeiba.com/horse/search_detail.html"
PER_PAGE = 100

# 何ページ連続で 0 件なら「構造が変わった」とみなして止めるか。
CONSECUTIVE_EMPTY_LIMIT = 3

# 「7,943件中  1から100件目」から総件数を取る。
_TOTAL_RE = re.compile(r"^([\d,]+)件中")

# 厩舎名の "[東]" "[西]" "[地]" などの接頭辞。
_STABLE_PREFIX = re.compile(r"^\[.+?\]")

_HEADER_MAP = {
    "馬名": "name",
    "性": "sex",
    "性別": "sex",
    "生年": "birth_year",
    "厩舎": "stable",
    "父": "sire",
    "母": "broodmare",
    "母父": "broodmare_sire",
    "馬主": "owner",
}


@dataclass
class HorseRow:
    """一覧 1 行ぶん。"""

    netkeiba_horse_id: str
    name: str | None
    sex: str | None
    birth_year: int | None
    sire: str | None = None
    broodmare: str | None = None
    broodmare_sire: str | None = None
    stable: str | None = None
    owner: str | None = None


@dataclass
class ListResult:
    """1 実行ぶんの結果。"""

    total_reported: int = 0
    pages_fetched: int = 0
    parsed: int = 0
    saved: int = 0
    birth_year_mismatch: int = 0
    errors: int = 0
    aborted_reason: str | None = None
    empty_pages: list[int] = field(default_factory=list)


def horse_age(birth_year: int, today: date) -> int:
    """netkeiba の馬齢を求める。

    日本の馬齢は「その年 − 生年」（1月1日で加齢）。**実行年を固定で書かないこと。**
    """
    return today.year - birth_year


def parse_total(page: str) -> int:
    """「N件中」から総件数を返す。取れなければ 0。"""
    soup = BeautifulSoup(page, "html.parser")
    for t in soup.stripped_strings:
        m = _TOTAL_RE.match(t.strip())
        if m:
            return int(m.group(1).replace(",", ""))
    return 0


def _attr(tag: Tag, name: str) -> str:
    """タグ属性を必ず str で返す。

    bs4 の `Tag.get()` は多値属性のために `str | AttributeValueList | None` を
    返す。`session.py` の同名ヘルパと同じ理由（型検査で落ちる）。
    """
    value = tag.get(name)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value[0]) if value else ""


def _first_link_text(cell: Tag) -> str | None:
    """セル内の最初の `<a>` の文字列を返す。

    父・母・母父のセルには詳細ページへのアイコンリンクが同居しており、
    `get_text()` だと `Palace Pier[]` のように括弧が混ざる。
    """
    a = cell.find("a")
    if isinstance(a, Tag):
        title = _attr(a, "title").strip()
        if title:
            return title
        txt = a.get_text(strip=True)
        return txt or None
    txt = cell.get_text(strip=True)
    return txt or None


def _clean_stable(value: str | None) -> str | None:
    """厩舎名から "[東]" などの接頭辞を除く。"""
    if not value:
        return None
    return _STABLE_PREFIX.sub("", value).strip() or None


def _build_col_map(header: Tag) -> dict[str, int]:
    col_map: dict[str, int] = {}
    for i, cell in enumerate(c for c in header.find_all(["th", "td"]) if isinstance(c, Tag)):
        label = cell.get_text(strip=True)
        # 見出しにはソート用の "↑↓" が付く
        label = label.replace("↑", "").replace("↓", "").strip()
        if label in _HEADER_MAP:
            col_map[_HEADER_MAP[label]] = i
    return col_map


def parse_rows(page: str) -> list[HorseRow]:
    """一覧ページから馬の行を取り出す。

    見出しから列位置を決める（列順の変更に耐えるため）。
    """
    soup = BeautifulSoup(page, "html.parser")
    table: Tag | None = None
    for t in soup.find_all("table"):
        if isinstance(t, Tag) and t.find("a", href=re.compile(r"/horse/\w+/")):
            table = t
            break
    if table is None:
        return []

    rows = [r for r in table.find_all("tr") if isinstance(r, Tag)]
    if len(rows) < 2:
        return []

    col_map = _build_col_map(rows[0])
    if "name" not in col_map:
        logger.warning("一覧の見出しに「馬名」がありません。構造が変わった可能性")
        return []

    out: list[HorseRow] = []
    for tr in rows[1:]:
        cells = [c for c in tr.find_all("td") if isinstance(c, Tag)]
        if not cells:
            continue
        name_idx = col_map["name"]
        if name_idx >= len(cells):
            continue
        link = cells[name_idx].find("a", href=re.compile(r"/horse/\w+/"))
        if not isinstance(link, Tag):
            continue
        m = re.search(r"/horse/(\w+)/", _attr(link, "href"))
        if not m:
            continue

        def cell(key: str) -> str | None:
            idx = col_map.get(key)
            if idx is None or idx >= len(cells):
                return None
            return _first_link_text(cells[idx])

        year_text = cell("birth_year")
        birth_year = int(year_text) if year_text and year_text.isdigit() else None

        out.append(
            HorseRow(
                netkeiba_horse_id=m.group(1),
                name=_first_link_text(cells[name_idx]),
                sex=cell("sex"),
                birth_year=birth_year,
                sire=cell("sire"),
                broodmare=cell("broodmare"),
                broodmare_sire=cell("broodmare_sire"),
                stable=_clean_stable(cell("stable")),
                owner=cell("owner"),
            )
        )
    return out


def fetch_page(
    http: requests.Session, limiter: RateLimiter, *, age: int, page: int
) -> str:
    """一覧の 1 ページを取る。"""
    params = {
        "range": "all",
        "word": "",
        "match": "p",
        "age_f": str(age),
        "age_t": str(age),
        "sort": "name-asc",
        "limit": str(PER_PAGE),
        "page": str(page),
    }
    limiter.wait()
    response = http.get(
        LIST_URL, params=params, headers={"Referer": SEARCH_PAGE}, timeout=40
    )
    response.raise_for_status()
    return decode_page(response)


UPSERT_SQL = text(
    """
    INSERT INTO keiba.pog_horses
        (netkeiba_horse_id, name, sex, birth_year,
         sire, broodmare, broodmare_sire, stable, owner)
    VALUES
        (:netkeiba_horse_id, :name, :sex, :birth_year,
         :sire, :broodmare, :broodmare_sire, :stable, :owner)
    ON CONFLICT (netkeiba_horse_id) DO UPDATE SET
        name           = COALESCE(EXCLUDED.name,           keiba.pog_horses.name),
        sex            = COALESCE(EXCLUDED.sex,            keiba.pog_horses.sex),
        birth_year     = COALESCE(EXCLUDED.birth_year,     keiba.pog_horses.birth_year),
        sire           = COALESCE(EXCLUDED.sire,           keiba.pog_horses.sire),
        broodmare      = COALESCE(EXCLUDED.broodmare,      keiba.pog_horses.broodmare),
        broodmare_sire = COALESCE(EXCLUDED.broodmare_sire, keiba.pog_horses.broodmare_sire),
        stable         = COALESCE(EXCLUDED.stable,         keiba.pog_horses.stable),
        owner          = COALESCE(EXCLUDED.owner,          keiba.pog_horses.owner),
        updated_at     = now()
    """
)


def upsert(session: Session, rows: list[HorseRow]) -> int:
    """`keiba.pog_horses` へ UPSERT して件数を返す。

    既存の値を NULL で潰さない（一覧に出ない項目があるため COALESCE）。
    """
    if not rows:
        return 0
    session.execute(UPSERT_SQL, [r.__dict__ for r in rows])
    return len(rows)


def scrape(
    session: Session,
    *,
    birth_year: int,
    today: date,
    http: requests.Session,
    limiter: RateLimiter | None = None,
    from_page: int = 1,
    to_page: int | None = None,
    dry_run: bool = False,
    environment_id: str = "local",
) -> ListResult:
    """指定した生産年の馬一覧を取得して `keiba.pog_horses` へ入れる。

    Args:
        birth_year: 生産年（例: 2024）。馬齢はここから実行日で求める。
        today: 実行日（JST の日付を渡す）。
        http: HTTP セッション。ログインは不要だが、既存のものを使い回してよい。
        from_page / to_page: 中断からの再開・部分取得用。
        dry_run: DB へ書かない。
    """
    result = ListResult()
    limiter = limiter or RateLimiter()
    age = horse_age(birth_year, today)
    if age < 0:
        result.aborted_reason = f"生産年 {birth_year} は未来です"
        logger.error("%s", result.aborted_reason)
        return result

    try:
        ip_restriction.require_not_restricted(session, context="pog-horse-list")
    except ip_restriction.IPRestricted as e:
        logger.warning("%s", e)
        result.aborted_reason = str(e)
        return result

    try:
        first = fetch_page(http, limiter, age=age, page=from_page)
    except Exception as e:  # noqa: BLE001
        if ip_restriction.looks_like_ip_restriction(str(e)):
            ip_restriction.mark_restricted(
                session, source="pog-horse-list", environment_id=environment_id
            )
        result.aborted_reason = f"1 ページ目が取得できません: {e}"
        logger.error("%s", result.aborted_reason)
        return result

    result.total_reported = parse_total(first)
    if result.total_reported == 0:
        result.aborted_reason = (
            "総件数を読めません。netkeiba の構造が変わった可能性があります"
        )
        logger.error("%s", result.aborted_reason)
        return result

    total_pages = math.ceil(result.total_reported / PER_PAGE)
    last_page = min(to_page, total_pages) if to_page else total_pages
    logger.info(
        "生産年 %d（%d歳）: 総件数 %d / %d ページ → %d〜%d ページを処理",
        birth_year, age, result.total_reported, total_pages, from_page, last_page,
    )

    consecutive_empty = 0
    for page_no in range(from_page, last_page + 1):
        try:
            page = first if page_no == from_page else fetch_page(
                http, limiter, age=age, page=page_no
            )
        except Exception as e:  # noqa: BLE001
            if ip_restriction.looks_like_ip_restriction(str(e)):
                ip_restriction.mark_restricted(
                    session, source="pog-horse-list", environment_id=environment_id
                )
                result.aborted_reason = f"IP 制限を検出しました: {e}"
                logger.error("%s", result.aborted_reason)
                return result
            logger.warning("page %d の取得に失敗: %s", page_no, e)
            result.errors += 1
            consecutive_empty += 1
            if consecutive_empty >= CONSECUTIVE_EMPTY_LIMIT:
                result.aborted_reason = f"{consecutive_empty} ページ連続で取得できません"
                return result
            continue

        result.pages_fetched += 1
        rows = parse_rows(page)
        if not rows:
            logger.warning("page %d: 行が取れません（構造変化の可能性）", page_no)
            result.empty_pages.append(page_no)
            consecutive_empty += 1
            if consecutive_empty >= CONSECUTIVE_EMPTY_LIMIT:
                result.aborted_reason = (
                    f"{consecutive_empty} ページ連続で行が取れません。"
                    "netkeiba の構造が変わった可能性があります"
                )
                logger.error("%s", result.aborted_reason)
                return result
            continue
        consecutive_empty = 0

        # 🔴 要求した世代が返っているか。馬齢の意味が変われば黙って別世代を取る。
        matched = [r for r in rows if r.birth_year == birth_year]
        result.birth_year_mismatch += len(rows) - len(matched)
        if not matched:
            result.aborted_reason = (
                f"page {page_no}: 生年が要求 {birth_year} と 1 件も一致しません"
                f"（age_f={age} の意味が変わった可能性）"
            )
            logger.error("%s", result.aborted_reason)
            return result

        result.parsed += len(matched)
        if not dry_run:
            result.saved += upsert(session, matched)
            session.commit()
        logger.info(
            "  page %d/%d: %d 頭（累計 %d/%d）",
            page_no, last_page, len(matched), result.parsed, result.total_reported,
        )

    return result
