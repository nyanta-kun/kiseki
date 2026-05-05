"""netkeiba から JRA 未登録2歳馬データをスクレイプして provisional_horses へ登録する。

## 効率化の設計

netkeiba の horse_id は「生産年4桁 + 6桁連番」（例: 2024100001）で単調増加する。
これを利用して「前回確認済みの最大 ID より後のものだけを対象にする」
インクリメンタル方式を採用し、全走査を回避する。

### 状態管理ファイル (.scrape_state/{year}.json)
```json
{"last_known_id": "2024103456", "scanned_at": "2026-05-04T12:00:00"}
```
- 初回: 0 からスキャン（全頭対象）
- 2回目以降: last_known_id + 1 からスキャン

### 早期終了
一覧ページを「最新登録順」で取得し、既知ID が EARLY_STOP_THRESHOLD 件
連続したらそのページで打ち切る（後続ページは全て既知と判断）。

### 実行:
  cd backend
  .venv/bin/python scripts/scrape_netkeiba_2yo.py              # 今年の2歳馬（デフォルト）
  .venv/bin/python scripts/scrape_netkeiba_2yo.py --year 2024  # 生産年指定
  .venv/bin/python scripts/scrape_netkeiba_2yo.py --full-scan  # 強制全件走査（初回のみ推奨）
  .venv/bin/python scripts/scrape_netkeiba_2yo.py --dry-run    # DB登録せず件数のみ表示
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8003")
API_KEY = os.environ.get("API_KEY", "")
NETKEIBA_USER = os.environ.get("NETKEIBA_USER") or os.environ.get("NETKEIBA_USER_ID", "")
NETKEIBA_PASS = os.environ.get("NETKEIBA_PASS") or os.environ.get("NETKEIBA_PASSWORD", "")

LIST_URL = "https://db.netkeiba.com/"
DETAIL_URL = "https://db.netkeiba.com/horse/{horse_id}/"
PED_URL = "https://db.netkeiba.com/horse/ped/{horse_id}/"
LOGIN_URL = "https://regist.netkeiba.com/account/"

STATE_DIR = Path(__file__).parent.parent / ".scrape_state"
BATCH_SIZE = 50
WAIT_BETWEEN_PAGES = 3.0
WAIT_BETWEEN_DETAILS = 1.5
# 一覧ページで既知IDがこの件数連続したら後続ページを打ち切る
EARLY_STOP_THRESHOLD = 20

SEX_MAP = {"牡": "牡", "牝": "牝", "セン": "セン", "騸": "セン"}
COAT_COLOR_MAP = {
    "栗毛", "鹿毛", "黒鹿毛", "青鹿毛", "青毛", "芦毛", "白毛", "栃栗毛",
}


# ---------------------------------------------------------------------------
# 状態管理
# ---------------------------------------------------------------------------

def _state_path(year: int) -> Path:
    STATE_DIR.mkdir(exist_ok=True)
    return STATE_DIR / f"{year}.json"


def load_state(year: int) -> dict:
    path = _state_path(year)
    if path.exists():
        return json.loads(path.read_text())
    return {"last_known_id": f"{year}000000", "scanned_at": None}


def save_state(year: int, last_known_id: str) -> None:
    path = _state_path(year)
    path.write_text(json.dumps({
        "last_known_id": last_known_id,
        "scanned_at": datetime.now().isoformat(),
    }))
    logger.info("状態保存: last_known_id=%s → %s", last_known_id, path)


# ---------------------------------------------------------------------------
# netkeiba セッション
# ---------------------------------------------------------------------------

def _decode(content: bytes) -> str:
    return content.decode("euc-jp", errors="replace")


def create_session() -> httpx.Client:
    """netkeibaにログインしてセッションを返す。ログイン情報がなければ匿名セッション。"""
    client = httpx.Client(
        follow_redirects=True,
        timeout=30.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
    )
    if NETKEIBA_USER and NETKEIBA_PASS:
        client.get(f"{LOGIN_URL}?pid=login")
        client.post(
            LOGIN_URL,
            data={
                "pid": "login",
                "action": "auth",
                "login_id": NETKEIBA_USER,
                "pswd": NETKEIBA_PASS,
                "return_url2": "",
                "mem_tp": "",
            },
        )
        client.get("https://www.netkeiba.com/")
        if "nkauth" in client.cookies:
            logger.info("netkeiba ログイン成功")
        else:
            logger.warning("netkeiba ログイン失敗 — 匿名モードで続行")
    return client


# ---------------------------------------------------------------------------
# 一覧ページ取得
# ---------------------------------------------------------------------------

def _birth_year_to_age(birth_year: int) -> int:
    """生産年から現在の馬齢を計算する（日本式: 1月1日を基準に加算）。"""
    from datetime import date
    return date.today().year - birth_year


def fetch_horse_list_page(
    client: httpx.Client, birth_year: int, page: int
) -> tuple[list[str], bool]:
    """指定ページの馬ID一覧を返す。(horse_ids, has_next_page)

    netkeiba の馬一覧は年齢（under_age/over_age）で検索する。
    生産年 2024 → 2026年時点では age=2。
    """
    age = _birth_year_to_age(birth_year)
    resp = client.get(
        LIST_URL,
        params={
            "pid": "horse_list",
            "under_age": str(age),
            "over_age": str(age),
            "list": "100",
            "page": str(page),
            "sort": "birthyear",
        },
    )
    if resp.status_code == 404:
        return [], False
    resp.raise_for_status()
    text = _decode(resp.content)

    horse_ids = list(dict.fromkeys(re.findall(r'/horse/(\d{10})/', text)))
    # 次ページリンクの有無で判定
    has_next = bool(re.search(r'次のページ|class="next"', text))
    return horse_ids, has_next


def collect_new_horse_ids(
    client: httpx.Client,
    birth_year: int,
    last_known_id: str,
    full_scan: bool,
    known_set: set[str],
) -> tuple[list[str], str]:
    """一覧ページを走査して未知の horse_id を収集する。

    インクリメンタルモード（full_scan=False）:
      - ID が last_known_id 以下なら「既知」としてカウント
      - EARLY_STOP_THRESHOLD 件連続で既知 → 以降のページを打ち切り

    Returns:
        (new_ids, max_id_seen)  max_id_seen は今回確認した最大IDで state に保存する
    """
    new_ids: list[str] = []
    max_id_seen = last_known_id
    page = 1

    while True:
        logger.info("一覧取得: year=%d, page=%d (last_known=%s)", birth_year, page, last_known_id)
        ids, has_next = fetch_horse_list_page(client, birth_year, page)
        if not ids:
            break

        consecutive_known = 0
        for hid in ids:
            if hid > max_id_seen:
                max_id_seen = hid

            is_known = hid in known_set or (not full_scan and hid <= last_known_id)
            if is_known:
                consecutive_known += 1
            else:
                consecutive_known = 0
                new_ids.append(hid)

        logger.info(
            "  → このページ: %d件中 %d件が新規 (連続既知=%d)",
            len(ids), sum(1 for hid in ids if hid not in known_set and (full_scan or hid > last_known_id)),
            consecutive_known,
        )

        if not full_scan and consecutive_known >= EARLY_STOP_THRESHOLD:
            logger.info("  → 早期終了: %d件連続で既知ID → 後続ページはスキップ", consecutive_known)
            break

        if not has_next:
            break

        page += 1
        time.sleep(WAIT_BETWEEN_PAGES)

    return new_ids, max_id_seen


# ---------------------------------------------------------------------------
# 詳細ページ取得
# ---------------------------------------------------------------------------

def _parse_name_from_td_text(raw: str) -> str:
    """血統テーブルのtdテキストから馬名だけを抽出する。
    例: 'ゴールドドリーム2013 鹿毛Halo系' → 'ゴールドドリーム'
        'フレンチデピュティFrench Deputy(米)1992 栗毛' → 'フレンチデピュティ'
        'ゴールドドリーム\\n\\t\\tHalo系' → 'ゴールドドリーム'
    """
    # [血統][産駒][FNo.x] 等のブラケット・FNo表記を除去
    raw = re.sub(r'\[.*?\]', '', raw)
    raw = re.sub(r'FNo\.\S*', '', raw)
    # 改行・タブで分割し、最初の非空行を馬名として使う
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # 4桁年以降を除去
        line = re.sub(r'\d{4}.*', '', line)
        # 英字・記号（系統名等）を除去
        line = re.sub(r'[A-Za-z()（）]+.*$', '', line)
        line = line.strip()
        if line:
            return line
    return ''


def fetch_blood_from_ped_page(client: httpx.Client, horse_id: str) -> dict[str, str]:
    """血統ページ（/horse/ped/{id}/）から父・母・母父を取得する。

    blood_table.detail の構造:
      - rowspan=16 の最初のtd = 父
      - rowspan=16 の2番目のtd = 母
      - 母の直後のtd (rowspan=8) = 母父
    """
    try:
        resp = client.get(PED_URL.format(horse_id=horse_id))
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        text = _decode(resp.content)

        # rowspan="16" のtdを正規表現で抽出（BeautifulSoupなしで処理）
        td_pattern = re.compile(
            r'<td[^>]+rowspan="16"[^>]*>(.*?)</td>',
            re.DOTALL
        )
        matches_16 = td_pattern.findall(text)

        result = {}
        for i, html in enumerate(matches_16[:2]):
            name = _parse_name_from_td_text(re.sub(r'<[^>]+>', '', html).strip())
            if name:
                if i == 0:
                    result["sire_name"] = name
                else:
                    result["dam_name"] = name

        # 母父: rowspan="8" のうち、dam_name の直後に現れるもの
        td8_pattern = re.compile(
            r'<td[^>]+rowspan="8"[^>]*>(.*?)</td>',
            re.DOTALL
        )
        # 母のtd位置以降で最初のrowspan=8を母父とする
        if "dam_name" in result:
            dam_td_pos = text.find(result["dam_name"])
            after_dam = text[dam_td_pos:] if dam_td_pos >= 0 else ""
            m8 = td8_pattern.search(after_dam)
            if m8:
                name = _parse_name_from_td_text(re.sub(r'<[^>]+>', '', m8.group(1)).strip())
                if name:
                    result["broodmare_sire_name"] = name

        return result
    except Exception as e:
        logger.warning("血統ページ取得失敗 %s: %s", horse_id, e)
        return {}


def fetch_horse_detail(client: httpx.Client, horse_id: str) -> dict[str, Any]:
    """馬詳細ページから基本情報を取得し、血統ページから父・母・母父を取得する。"""
    url = DETAIL_URL.format(horse_id=horse_id)
    resp = client.get(url)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    text = _decode(resp.content)

    result: dict[str, Any] = {"netkeiba_horse_id": horse_id}

    # 馬名
    name_m = re.search(r'<div class="horse_title"[^>]*>\s*<h1[^>]*>([^<]+)</h1>', text)
    if not name_m:
        name_m = re.search(r'<title>([^\|（]+)', text)
    if name_m:
        result["name"] = name_m.group(1).strip()

    def _td_after_th(label: str) -> str | None:
        m = re.search(
            rf'<th[^>]*>\s*{re.escape(label)}\s*</th>\s*<td[^>]*>(.*?)</td>',
            text, re.DOTALL
        )
        if not m:
            return None
        return re.sub(r'<[^>]+>', '', m.group(1)).strip() or None

    # 生年月日
    bdate_raw = _td_after_th("生年月日")
    if bdate_raw:
        bdate_m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', bdate_raw)
        if bdate_m:
            y, mo, d = bdate_m.groups()
            result["birth_year"] = int(y)
            result["birth_date"] = f"{y}{int(mo):02d}{int(d):02d}"

    # 性別・毛色
    # 詳細ページでは <th>性齢</th> がない場合が多く、
    # <p class="txt_01">現役　牡2歳　栗毛</p> に含まれる
    sex_color_raw = (
        _td_after_th("性齢") or _td_after_th("性別") or _td_after_th("性毛色")
        or re.search(r'class="txt_01"[^>]*>([^<]+)', text, re.DOTALL) and
        re.search(r'class="txt_01"[^>]*>([^<]+)', text, re.DOTALL).group(1)
    )
    if sex_color_raw:
        for sex_key in ("牡", "牝", "セン", "騸"):
            if sex_key in sex_color_raw:
                result["sex"] = SEX_MAP.get(sex_key)
                break
        for color_key in COAT_COLOR_MAP:
            if color_key in sex_color_raw:
                result["coat_color"] = color_key
                break

    result["trainer_name"] = _td_after_th("調教師")
    result["owner_name"] = _td_after_th("馬主")
    result["farm_name"] = _td_after_th("生産者") or _td_after_th("生産牧場")

    # 血統（父・母・母父）は血統ページから取得
    blood = fetch_blood_from_ped_page(client, horse_id)
    result.update(blood)

    return result


# ---------------------------------------------------------------------------
# バックエンド API 呼び出し
# ---------------------------------------------------------------------------

def get_known_horse_ids(birth_year: int) -> set[str]:
    """keiba.horses と provisional_horses の両方で既知の ID を返す。

    一括 API で取得することで一覧走査中の早期終了判定に使う。
    """
    try:
        resp = httpx.get(
            f"{BACKEND_URL}/api/horses/known-ids",
            params={"birth_year": birth_year},
            headers={"X-API-Key": API_KEY},
            timeout=15,
        )
        if resp.status_code == 200:
            return set(resp.json().get("ids", []))
    except Exception as e:
        logger.warning("既知ID取得失敗（スキップして続行）: %s", e)
    return set()


def post_provisional_horses(horses: list[dict[str, Any]]) -> dict:
    resp = httpx.post(
        f"{BACKEND_URL}/api/import/provisional-horses",
        json={"horses": horses},
        headers={"X-API-Key": API_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# DB直接更新（--fix-existing 用）
# ---------------------------------------------------------------------------

def _get_db_conn() -> psycopg2.extensions.connection:
    """環境変数から DB 接続を作成する。"""
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        return psycopg2.connect(db_url)
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        dbname=os.environ.get("DB_NAME", "hrdb"),
    )


def fix_existing_horses(birth_year: int, dry_run: bool) -> None:
    """provisional_horses の既存レコードを netkeiba から再取得して更新する。

    主に sire_name / dam_name / sex が欠落・誤っているレコードを修正するために使う。
    merged_horse_id が設定済みのレコードは変更しない。
    """
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT netkeiba_horse_id, name
                FROM keiba.provisional_horses
                WHERE birth_year = %s
                  AND merged_horse_id IS NULL
                ORDER BY netkeiba_horse_id
                """,
                (birth_year,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    logger.info("再取得対象: %d 頭 (birth_year=%d)", len(rows), birth_year)
    if not rows:
        return

    client = create_session()
    updated = 0
    failed = 0

    for i, (horse_id, name) in enumerate(rows):
        logger.info("[%d/%d] 再取得: %s (%s)", i + 1, len(rows), name, horse_id)
        try:
            detail = fetch_horse_detail(client, horse_id)
        except Exception as e:
            logger.warning("取得失敗 %s: %s", horse_id, e)
            failed += 1
            time.sleep(WAIT_BETWEEN_DETAILS)
            continue

        if not detail:
            logger.warning("データなし → スキップ: %s", horse_id)
            failed += 1
            time.sleep(WAIT_BETWEEN_DETAILS)
            continue

        if dry_run:
            logger.info(
                "[dry-run] %s → sex=%s, sire=%s, dam=%s, dam_sire=%s",
                name, detail.get("sex"), detail.get("sire_name"),
                detail.get("dam_name"), detail.get("broodmare_sire_name"),
            )
            updated += 1
        else:
            conn = _get_db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE keiba.provisional_horses
                        SET sex = %s,
                            coat_color = %s,
                            sire_name = %s,
                            dam_name = %s,
                            broodmare_sire_name = %s,
                            trainer_name = %s,
                            owner_name = %s,
                            farm_name = %s,
                            updated_at = NOW()
                        WHERE netkeiba_horse_id = %s
                          AND merged_horse_id IS NULL
                        """,
                        (
                            detail.get("sex"),
                            detail.get("coat_color"),
                            detail.get("sire_name"),
                            detail.get("dam_name"),
                            detail.get("broodmare_sire_name"),
                            detail.get("trainer_name"),
                            detail.get("owner_name"),
                            detail.get("farm_name"),
                            horse_id,
                        ),
                    )
                    conn.commit()
                logger.info("更新: %s → sex=%s, sire=%s, dam=%s",
                            name, detail.get("sex"), detail.get("sire_name"), detail.get("dam_name"))
                updated += 1
            except Exception as e:
                conn.rollback()
                logger.error("DB更新失敗 %s: %s", horse_id, e)
                failed += 1
            finally:
                conn.close()

        time.sleep(WAIT_BETWEEN_DETAILS)

    logger.info("完了: 更新=%d, 失敗=%d", updated, failed)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="netkeiba 2歳馬スクレイパー（インクリメンタル方式）")
    parser.add_argument("--year", type=int, default=date.today().year - 2,
                        help="生産年（デフォルト: 今年-2）")
    parser.add_argument("--full-scan", action="store_true",
                        help="前回状態を無視して全件走査（初回 or リセット時）")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB登録せず件数のみ表示")
    parser.add_argument("--fix-existing", action="store_true",
                        help="一覧走査をスキップし、既存の provisional_horses を netkeiba から再取得して更新する")
    args = parser.parse_args()

    birth_year = args.year

    # --fix-existing: 既存データを再取得して更新（一覧走査はスキップ）
    if args.fix_existing:
        logger.info("=== 既存 provisional_horses 再取得モード (birth_year=%d) ===", birth_year)
        fix_existing_horses(birth_year, args.dry_run)
        return

    state = load_state(birth_year)
    last_known_id = f"{birth_year}000000" if args.full_scan else state["last_known_id"]
    logger.info(
        "対象生産年: %d | last_known_id: %s | full_scan: %s",
        birth_year, last_known_id, args.full_scan
    )

    # バックエンドから既知IDをまとめて取得（一覧走査中の早期終了に使用）
    known_set = get_known_horse_ids(birth_year)
    logger.info("既知ID数: %d（keiba.horses + provisional_horses）", len(known_set))

    client = create_session()

    # --- インクリメンタル一覧走査 ---
    new_ids, max_id_seen = collect_new_horse_ids(
        client, birth_year, last_known_id, args.full_scan, known_set
    )
    logger.info("新規対象: %d 頭 (max_id_seen=%s)", len(new_ids), max_id_seen)

    if not new_ids:
        logger.info("新規登録対象なし。終了。")
        if max_id_seen > last_known_id:
            save_state(birth_year, max_id_seen)
        return

    if args.dry_run:
        logger.info("[dry-run] %d 頭を登録予定（実際には登録しない）", len(new_ids))
        return

    # --- 詳細ページをスクレイプしてバッチ送信 ---
    batch: list[dict[str, Any]] = []
    total_sent = 0

    for i, horse_id in enumerate(new_ids):
        logger.info("[%d/%d] 詳細取得: %s", i + 1, len(new_ids), horse_id)
        try:
            detail = fetch_horse_detail(client, horse_id)
        except Exception as e:
            logger.warning("詳細取得失敗 %s: %s", horse_id, e)
            detail = {}

        if detail.get("name"):
            batch.append(detail)
        else:
            logger.warning("馬名取得失敗 → スキップ: %s", horse_id)

        if len(batch) >= BATCH_SIZE:
            result = post_provisional_horses(batch)
            total_sent += len(batch)
            logger.info("送信完了: %s (累計%d頭)", result.get("stats"), total_sent)
            batch = []

        time.sleep(WAIT_BETWEEN_DETAILS)

    if batch:
        result = post_provisional_horses(batch)
        total_sent += len(batch)
        logger.info("送信完了: %s (累計%d頭)", result.get("stats"), total_sent)

    # 状態を更新（次回からはここから再開）
    save_state(birth_year, max_id_seen)
    logger.info("完了: 合計 %d 頭を provisional_horses へ登録", total_sent)


if __name__ == "__main__":
    main()
