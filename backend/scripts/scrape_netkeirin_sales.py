"""netkeirin「ウマい車券」予想家成績・売上（日別）を取得して keirin.netkeirin_sales_daily へ保存する。

対象ページ: https://umaiaggre.yosoka.netkeiba.com/tool_keirin/result/yosoka_result.html
（二軸探偵アカウントの「予想家成績状況」。同一URLへのPOSTでHTMLがサーバー側
レンダリングされて返る。list_detail=day を指定すると集計ID=YYYYMMDD の日別行が
1レコードずつ得られる）。

認証: **netkeirin 入稿ツールと同じ資格情報**（.env の NETKEIRIN_LOGIN_ID /
NETKEIRIN_PASSWORD）で、このツール自身のログインAPIへPOSTする。

    POST https://umaiaggre.yosoka.netkeiba.com/tool_keirin/auth/api_post_login.html
    data: {output: "json", action: "login", user_id: <ID>, password: <PW>}
    成功時: {"status": "OK", ...}

⚠️ 当初実装は `regist.netkeiba.com` へログインし「nkauth Cookie はドメイン
.netkeiba.com なのでサブドメインにも送られる」という前提だったが、**これは誤り**
だった（2026-08-03 実機で判明）。umaiaggre.yosoka.netkeiba.com は独自の認証を持ち、
nkauth だけでは `tool_keirin/auth/login.html` へリダイレクトされる。当時のコードは
nkauth Cookie の有無しか見ていなかったため「ログイン成功」と表示しつつ実際には
未認証で、結果テーブルが取れず0件になっていた。ログイン画面にも
「入稿ツールと同じID・パスワードとなります」と明記されている。
そのため keirin リポジトリ `src/netkeirin_client.py::login()` と同じ方式へ統一した。

集計は「通常集計日はレース日の翌日」「売上は速報値」（ページ注記どおり）。
そのため当日分は未確定であり、日次バッチは前日分を再取得して UPSERT で
上書きする運用とする（過去分も値が動くことがあるため、バックフィル時も
同様にUPSERTで安全に再実行できる）。

使い方:
    # 前日分のみ取得（LaunchAgent からの日次実行を想定）
    .venv/bin/python scripts/scrape_netkeirin_sales.py

    # 期間指定
    .venv/bin/python scripts/scrape_netkeirin_sales.py --from 2026-07-01 --to 2026-08-02

    # 直近N日のバックフィル（サイト側の制約で1回のPOSTにつき最大約1年まで）
    .venv/bin/python scripts/scrape_netkeirin_sales.py --backfill-days 365

    # DB書込みせずパース結果だけ確認したい場合
    .venv/bin/python scripts/scrape_netkeirin_sales.py --backfill-days 30 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import requests
from psycopg2.extras import execute_values

# python-dotenv は kiseki の venv にはあるが、VPS で本スクリプトを動かす
# keirin の venv には無い（B案でVPS実行に変更した際に判明・2026-08-03）。
# 他リポジトリの本番venvへ依存を追加せずに済むよう、未導入時は環境変数のみに
# フォールバックする（呼び出し元の scrape_netkeirin_sales.sh が必要な変数を
# export する）。
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ModuleNotFoundError:  # pragma: no cover - 実行環境依存
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)
BASE_URL = "https://umaiaggre.yosoka.netkeiba.com/tool_keirin"
LOGIN_URL = f"{BASE_URL}/auth/api_post_login.html"
SALES_URL = f"{BASE_URL}/result/yosoka_result.html"
# 未認証時のリダイレクト先。レスポンスがここに来ていたら認証が切れている。
LOGIN_PAGE_MARKER = "auth/login.html"
TIMEOUT = 30
JST = ZoneInfo("Asia/Tokyo")

# ページ表示順の列名 → DBカラム名。「集計ID」「集計名」は別途処理するため含めない。
_COLUMNS = [
    "n_predictions", "n_predictions_staked", "n_hits_incl_garami", "n_hits_excl_garami",
    "n_miss", "stake_amount", "payout_amount", "hit_rate_pct", "recovery_rate_pct",
    "n_sold", "sold_points", "sold_paid_points", "avg_sold_points", "avg_sold_minutes",
    "avg_sold_hour", "axis1_rate_1st", "axis1_rate_2nd", "axis1_rate_3rd",
    "mark2_count", "mark2_rate_1st", "mark2_rate_2nd", "mark2_rate_3rd",
    "mark3_count", "mark3_rate_1st", "mark3_rate_2nd", "mark3_rate_3rd",
    "mark123_count", "transition_axis1_to_mark2_pct", "transition_axis1_to_mark3_pct",
    "transition_mark2_to_axis1_pct", "transition_mark3_to_axis1_pct",
]


def login() -> requests.Session:
    """「ウマい車券」ツールにログインしたセッションを返す。

    keirin リポジトリ `src/netkeirin_client.py::login()` と同一方式
    （入稿ツールと同じ資格情報・同じログインAPI）。

    Cookie の有無ではなく **レスポンスJSONの status == "OK"** で判定する
    （当初実装は Cookie の有無だけを見ていたため、未認証でも「成功」と
    表示され0件で静かに終わっていた）。
    """
    uid = os.environ.get("NETKEIRIN_LOGIN_ID")
    pw = os.environ.get("NETKEIRIN_PASSWORD")
    if not uid or not pw:
        raise SystemExit(
            "NETKEIRIN_LOGIN_ID / NETKEIRIN_PASSWORD が環境変数・.env にありません"
            "（netkeirin入稿ツールと同じ資格情報。VPSでは keirin/.env が正）")
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ja-JP,ja"})
    try:
        r = s.post(
            LOGIN_URL,
            data={"output": "json", "action": "login", "user_id": uid, "password": pw},
            timeout=TIMEOUT,
        )
        ok = r.status_code == 200 and r.json().get("status") == "OK"
    except (requests.RequestException, ValueError) as e:
        raise SystemExit(f"ログインリクエスト失敗: {e}") from e
    if not ok:
        raise SystemExit(f"ログイン失敗: status={r.status_code} body={r.text[:200]}")
    logger.info("netkeirin(ウマい車券) ログイン成功")
    return s


def _to_number(text: str) -> float | None:
    """"180,000" / "70%" / "" → 180000.0 / 70.0 / None。"""
    t = text.strip().replace(",", "").replace("%", "")
    if t in ("", "-", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def fetch_daily_rows(session: requests.Session, date_from: str, date_to: str) -> list[dict]:
    """cnd_from/cnd_to(YYYY-MM-DD) 範囲の日別集計行を取得してdictのリストで返す。

    サイト側の制約「期間は最大1年程度」を超える範囲を渡すと空/一部欠落になり
    得るため、呼び出し側で1年以内に区切ること（--backfill-days はこのモジュール
    内で自動的に分割する）。
    """
    body = {
        "cnd_from": date_from,
        "cnd_to": date_to,
        "list_sya_num_from": "1",
        "list_sya_num_to": "9",
        "list_detail": "day",
        "dispUpdate": "検索",
    }
    r = session.post(SALES_URL, data=body, timeout=TIMEOUT)
    r.raise_for_status()
    html = r.text

    # 認証切れはログイン画面へリダイレクトされる。ここで気付かないと
    # 「テーブルが無い＝0件」として静かに成功扱いになる（当初実装の失敗モード）。
    if LOGIN_PAGE_MARKER in str(r.url):
        raise SystemExit(
            f"認証されていません（{r.url} へリダイレクト）。"
            "NETKEIRIN_LOGIN_ID / NETKEIRIN_PASSWORD を確認してください")

    table_match = re.search(r"<table.*?</table>", html, re.DOTALL)
    if not table_match:
        logger.warning("結果テーブルが見つかりません（%s〜%s）", date_from, date_to)
        return []
    table_html = table_match.group(0)

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)
    results: list[dict] = []
    for row in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)]
        if not cells or not re.fullmatch(r"\d{8}", cells[0]):
            continue  # ヘッダー行や集計対象外の行(合計等)をスキップ
        sale_date = cells[0]
        values = cells[2:]  # cells[1] は「集計名」(例:"日別")
        if len(values) < len(_COLUMNS):
            logger.warning("列数不足のためスキップ: %s (got %d cols)", sale_date, len(values))
            continue
        record = {"sale_date": sale_date}
        for col, raw in zip(_COLUMNS, values):
            record[col] = _to_number(raw)
        # 整数であるべき列はintに丸める（NULLはそのまま）
        for col in (
            "n_predictions", "n_predictions_staked", "n_hits_incl_garami", "n_hits_excl_garami",
            "n_miss", "stake_amount", "payout_amount", "n_sold", "sold_points",
            "sold_paid_points", "mark2_count", "mark3_count", "mark123_count",
        ):
            if record.get(col) is not None:
                record[col] = int(record[col])
        results.append(record)
    return results


UPSERT_SQL = f"""
INSERT INTO keirin.netkeirin_sales_daily
  (sale_date, {", ".join(_COLUMNS)}, collected_at)
VALUES %s
ON CONFLICT (sale_date) DO UPDATE SET
  {", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNS)},
  collected_at = now()
"""


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )


def save_db(records: list[dict]) -> int:
    if not records:
        return 0
    rows = [
        tuple([r["sale_date"]] + [r.get(c) for c in _COLUMNS] + [datetime.now(JST)])
        for r in records
    ]
    conn = psycopg2.connect(_dsn())
    with conn, conn.cursor() as cur:
        execute_values(cur, UPSERT_SQL, rows)
    conn.close()
    return len(rows)


def _daterange_chunks(start: date, end: date, max_days: int = 360) -> list[tuple[date, date]]:
    """開始日〜終了日を max_days 以内のチャンクに分割する（サイト側「最大1年程度」対策）。"""
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=max_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD（省略時は前日 or --backfill-days に従う）")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD（省略時は前日 or --backfill-days に従う）")
    ap.add_argument("--backfill-days", type=int, help="今日を起点に過去N日分を取得（--from/--to省略時のみ有効）")
    ap.add_argument("--dry-run", action="store_true", help="DBに書き込まず取得件数と先頭数件のみ表示")
    args = ap.parse_args()

    today_jst = datetime.now(JST).date()
    if args.date_from and args.date_to:
        start = datetime.strptime(args.date_from, "%Y-%m-%d").date()
        end = datetime.strptime(args.date_to, "%Y-%m-%d").date()
    elif args.backfill_days:
        end = today_jst - timedelta(days=1)
        start = end - timedelta(days=args.backfill_days - 1)
    else:
        # 通常の日次実行: 前日分のみ（「通常集計日はレース日の翌日」に対応）
        start = end = today_jst - timedelta(days=1)

    logger.info("取得期間: %s 〜 %s", start, end)
    session = login()

    all_records: list[dict] = []
    for chunk_from, chunk_to in _daterange_chunks(start, end):
        logger.info("チャンク取得: %s 〜 %s", chunk_from, chunk_to)
        recs = fetch_daily_rows(session, chunk_from.isoformat(), chunk_to.isoformat())
        logger.info("  -> %d 件", len(recs))
        all_records.extend(recs)

    if args.dry_run:
        logger.info("dry-run: 合計 %d 件取得（DB書き込みはスキップ）", len(all_records))
        for r in all_records[:5]:
            logger.info("  %s", r)
        return

    n = save_db(all_records)
    logger.info("保存完了: %d 件を keirin.netkeirin_sales_daily に UPSERT", n)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        logger.error(str(e))
        sys.exit(1)
