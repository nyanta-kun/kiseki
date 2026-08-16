"""netkeirin「ウマい車券」予想家成績・売上を取得して keirin.netkeirin_sales_* へ保存する。

対象ページ: https://umaiaggre.yosoka.netkeiba.com/tool_keirin/result/yosoka_result.html
（二軸探偵アカウントの「予想家成績状況」。同一URLへのPOSTでHTMLがサーバー側
レンダリングされて返る。list_detail で粒度を選ぶ）。

    list_detail=day  → 集計ID=YYYYMMDD の日別行     → keirin.netkeirin_sales_daily
    list_detail=race → 集計ID=YYYYMMDD+場2桁+R2桁   → keirin.netkeirin_sales_race

**列構成は両者で完全に同一**で、違うのは集計IDの桁数と集計名（レース別は
"08/10 四日市 Ａ級 準決勝"）だけ。そのため取得・パースは共通で、保存先だけ分ける。
レース別は「どのレースが売れたか／当たったか」という日別からは復元できない
粒度なので、日別と別テーブルに一次資料のまま保持する（Web の分析タブが使う）。

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
    # 前日分を日別・レース別とも取得（cron からの日次実行を想定）
    .venv/bin/python scripts/scrape_netkeirin_sales.py

    # 期間指定
    .venv/bin/python scripts/scrape_netkeirin_sales.py --from 2026-07-01 --to 2026-08-02

    # 直近N日のバックフィル（サイト側の制約で1回のPOSTにつき最大約1年まで）
    .venv/bin/python scripts/scrape_netkeirin_sales.py --backfill-days 365

    # 粒度を絞る（既定は both）
    .venv/bin/python scripts/scrape_netkeirin_sales.py --detail race

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


# int に丸める列（NULL はそのまま）。率(%)・平均は float のまま保持する。
_INT_COLUMNS = (
    "n_predictions", "n_predictions_staked", "n_hits_incl_garami", "n_hits_excl_garami",
    "n_miss", "stake_amount", "payout_amount", "n_sold", "sold_points",
    "sold_paid_points", "mark2_count", "mark3_count", "mark123_count",
)


def _parse_table(html: str, id_pattern: str) -> list[tuple[str, str, dict]]:
    """結果テーブルから (集計ID, 集計名, 指標dict) を取り出す。

    id_pattern は集計IDの形（日別 `\\d{8}` / レース別 `\\d{12}`）。ヘッダー行や
    合計行は集計IDが一致しないので自然に落ちる。
    """
    table_match = re.search(r"<table.*?</table>", html, re.DOTALL)
    if not table_match:
        return []
    table_html = table_match.group(0)

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)
    results: list[tuple[str, str, dict]] = []
    for row in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)]
        if not cells or not re.fullmatch(id_pattern, cells[0]):
            continue  # ヘッダー行や集計対象外の行(合計等)をスキップ
        agg_id = cells[0]
        agg_name = cells[1] if len(cells) > 1 else ""
        values = cells[2:]  # cells[1] は「集計名」(日別なら"日別"・レース別ならレース名)
        if len(values) < len(_COLUMNS):
            logger.warning("列数不足のためスキップ: %s (got %d cols)", agg_id, len(values))
            continue
        metrics = {col: _to_number(raw) for col, raw in zip(_COLUMNS, values)}
        for col in _INT_COLUMNS:
            if metrics.get(col) is not None:
                metrics[col] = int(metrics[col])
        results.append((agg_id, agg_name, metrics))
    return results


def _post_result_page(
    session: requests.Session, date_from: str, date_to: str, detail: str
) -> str:
    """結果ページへ検索POSTしてHTMLを返す。認証切れならその場で落とす。"""
    body = {
        "cnd_from": date_from,
        "cnd_to": date_to,
        "list_sya_num_from": "1",
        "list_sya_num_to": "9",
        "list_detail": detail,
        "dispUpdate": "検索",
    }
    r = session.post(SALES_URL, data=body, timeout=TIMEOUT)
    r.raise_for_status()

    # 認証切れはログイン画面へリダイレクトされる。ここで気付かないと
    # 「テーブルが無い＝0件」として静かに成功扱いになる（当初実装の失敗モード）。
    if LOGIN_PAGE_MARKER in str(r.url):
        raise SystemExit(
            f"認証されていません（{r.url} へリダイレクト）。"
            "NETKEIRIN_LOGIN_ID / NETKEIRIN_PASSWORD を確認してください")
    return r.text


def fetch_daily_rows(session: requests.Session, date_from: str, date_to: str) -> list[dict]:
    """cnd_from/cnd_to(YYYY-MM-DD) 範囲の日別集計行を取得してdictのリストで返す。

    サイト側の制約「期間は最大1年程度」を超える範囲を渡すと空/一部欠落になり
    得るため、呼び出し側で1年以内に区切ること（--backfill-days はこのモジュール
    内で自動的に分割する）。
    """
    html = _post_result_page(session, date_from, date_to, "day")
    parsed = _parse_table(html, r"\d{8}")
    if not parsed:
        logger.warning("日別の結果行が見つかりません（%s〜%s）", date_from, date_to)
    return [{"sale_date": agg_id, **metrics} for agg_id, _name, metrics in parsed]


def race_fields(agg_id: str) -> dict:
    """レース別の集計ID(12桁)を kiseki 側のキーへ分解する。

    `202608104808` → 20260810 / 場48 / 8R / race_key `20260810_48_08`。
    ⚠️ **race_key のレース番号はゼロ埋め2桁**（picks_history / wt_races と同形式）。
       ここがずれると join が全滅してランクも開催時間帯も付かないが、
       売上そのものは出るので画面を見ても気づけない。
    """
    race_date, venue_code, race_no_str = agg_id[:8], agg_id[8:10], agg_id[10:12]
    return {
        "race_id": agg_id,
        "race_date": race_date,
        "venue_code": venue_code,
        "race_no": int(race_no_str),
        "race_key": f"{race_date}_{venue_code}_{race_no_str}",
    }


def fetch_race_rows(session: requests.Session, date_from: str, date_to: str) -> list[dict]:
    """同期間のレース別集計行を取得してdictのリストで返す。

    集計IDは `YYYYMMDD` + 場コード2桁 + レース番号2桁 の12桁。場コードは kiseki の
    keirin.venue_info.venue_code と同一体系なので、`race_key`(YYYYMMDD_VV_RR) を
    ここで組み立てて wt_races / picks_history と結合できるようにしておく。
    """
    html = _post_result_page(session, date_from, date_to, "race")
    parsed = _parse_table(html, r"\d{12}")
    if not parsed:
        logger.warning("レース別の結果行が見つかりません（%s〜%s）", date_from, date_to)
    return [
        {**race_fields(agg_id), "race_label": agg_name[:120] or None, **metrics}
        for agg_id, agg_name, metrics in parsed
    ]


UPSERT_SQL = f"""
INSERT INTO keirin.netkeirin_sales_daily
  (sale_date, {", ".join(_COLUMNS)}, collected_at)
VALUES %s
ON CONFLICT (sale_date) DO UPDATE SET
  {", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNS)},
  collected_at = now()
"""

_RACE_KEY_COLUMNS = ["race_date", "venue_code", "race_no", "race_key", "race_label"]

UPSERT_RACE_SQL = f"""
INSERT INTO keirin.netkeirin_sales_race
  (race_id, {", ".join(_RACE_KEY_COLUMNS + _COLUMNS)}, collected_at)
VALUES %s
ON CONFLICT (race_id) DO UPDATE SET
  {", ".join(f"{c} = EXCLUDED.{c}" for c in _RACE_KEY_COLUMNS + _COLUMNS)},
  collected_at = now()
"""


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )


# 売上の計算・文面・送信は `src/services/keirin_sales_report.py` が正本。
# 🔴 **数値も文面もここへ写さないこと。** Web（`/api/keirin/netkeirin-sales`）と
#    同じモジュールを読むことで、画面と Discord で売上が食い違わないようにしている。
# ⚠️ 向こうは**標準ライブラリだけ**で書いてある。VPS ではこのスクリプトが
#    keirin の venv（FastAPI も SQLAlchemy も無い）で動くため。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.keirin_sales_report import (  # noqa: E402
    build_sales_message, post_to_discord,
)

#: 通知先の Discord チャンネル（keirin/.env の webhook URL 環境変数名）
_NOTIFY_ENV_KEY = "DISCORD_WEBHOOK_URL_NETKEIRIN"


def fetch_sales_summary(sale_date: date) -> dict | None:
    """指定日の販売実績と、その日が属する月の売上合計を DB から読む。

    ⚠️ **スクレイプ結果ではなく DB を読む**。UPSERT 後の実際の値を出すためで、
       取り込みに失敗した列があれば通知にもそのまま現れる（黙って別の数字を
       出さない）。

    `sale_date` の列は **`YYYYMMDD` の文字列**（`YYYY-MM-DD` ではない）。
    月合計はその接頭辞6桁で絞る。

    returns 当日行が無ければ None
    """
    ymd = sale_date.strftime("%Y%m%d")
    conn = psycopg2.connect(_dsn())
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT n_sold, sold_points, sold_paid_points "
                "FROM keirin.netkeirin_sales_daily WHERE sale_date = %s", (ymd,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                "SELECT COUNT(*), COALESCE(SUM(sold_points), 0), "
                "       COALESCE(SUM(sold_paid_points), 0) "
                "FROM keirin.netkeirin_sales_daily WHERE sale_date LIKE %s",
                (ymd[:6] + "%",))
            n_days, month_points, month_paid = cur.fetchone()
    finally:
        conn.close()
    return {
        "sale_date": ymd,
        "n_sold": int(row[0] or 0),
        "sold_points": int(row[1] or 0),
        "sold_paid_points": int(row[2] or 0),
        "month_n_days": int(n_days or 0),
        "month_sold_points": int(month_points or 0),
        "month_sold_paid_points": int(month_paid or 0),
    }


def notify_sales(sale_date: date) -> bool:
    """当日ぶんの売上を Discord へ通知する。送れたら True。

    ⚠️ **通知の失敗で取り込みを落とさない**。売上データは既に DB へ入っており、
       通知はその報告でしかない。ここで例外を投げると、次の実行まで
       「スクレイプが落ちた」ように見える。
    """
    url = os.getenv(_NOTIFY_ENV_KEY, "")
    if not url:
        logger.warning("%s が未設定のため Discord 通知をスキップします", _NOTIFY_ENV_KEY)
        return False
    try:
        summary = fetch_sales_summary(sale_date)
    except Exception as e:  # noqa: BLE001 — 通知のための読み取りで本処理を落とさない
        logger.warning("売上サマリーを取得できません: %s", e)
        return False
    if summary is None:
        # 開催が無かった日・netkeirin 側の集計がまだの日。0円と書くと誤読するので出さない。
        logger.info("%s の行がまだ無いため Discord 通知をスキップします", sale_date)
        return False
    if not post_to_discord(url, build_sales_message(summary)):
        logger.warning("Discord 通知に失敗しました（%s）", summary["sale_date"])
        return False
    logger.info("Discord へ売上を通知しました（%s）", summary["sale_date"])
    return True


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


def save_race_db(records: list[dict]) -> int:
    if not records:
        return 0
    cols = ["race_id"] + _RACE_KEY_COLUMNS + _COLUMNS
    rows = [tuple([r.get(c) for c in cols] + [datetime.now(JST)]) for r in records]
    conn = psycopg2.connect(_dsn())
    with conn, conn.cursor() as cur:
        execute_values(cur, UPSERT_RACE_SQL, rows)
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
    ap.add_argument("--detail", choices=("day", "race", "both"), default="both",
                    help="取得粒度（既定 both: 日別とレース別の両方）")
    ap.add_argument("--dry-run", action="store_true", help="DBに書き込まず取得件数と先頭数件のみ表示")
    ap.add_argument("--no-notify", action="store_true",
                    help="Discord へ売上を通知しない（過去分の取り直し用）")
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

    want_day = args.detail in ("day", "both")
    want_race = args.detail in ("race", "both")
    logger.info("取得期間: %s 〜 %s（detail=%s）", start, end, args.detail)
    session = login()

    day_records: list[dict] = []
    race_records: list[dict] = []
    for chunk_from, chunk_to in _daterange_chunks(start, end):
        logger.info("チャンク取得: %s 〜 %s", chunk_from, chunk_to)
        if want_day:
            recs = fetch_daily_rows(session, chunk_from.isoformat(), chunk_to.isoformat())
            logger.info("  日別   -> %d 件", len(recs))
            day_records.extend(recs)
        if want_race:
            recs = fetch_race_rows(session, chunk_from.isoformat(), chunk_to.isoformat())
            logger.info("  レース別 -> %d 件", len(recs))
            race_records.extend(recs)

    if args.dry_run:
        logger.info("dry-run: 日別 %d 件 / レース別 %d 件（DB書き込みはスキップ）",
                    len(day_records), len(race_records))
        for r in (day_records + race_records)[:5]:
            logger.info("  %s", r)
        return

    if want_day:
        n = save_db(day_records)
        logger.info("保存完了: %d 件を keirin.netkeirin_sales_daily に UPSERT", n)
    if want_race:
        n = save_race_db(race_records)
        logger.info("保存完了: %d 件を keirin.netkeirin_sales_race に UPSERT", n)

    # 日次の売上を Discord へ報告する（2026-08-16・ユーザー要望）。
    # 🔴 **1日ぶんを取ったときだけ**送る。バックフィル（複数日）で送ると
    #    過去分の取り直しのたびに通知が何十件も飛ぶ。
    # ⚠️ 日別（`--detail day|both`）を取っていないときは送らない。売上の数字は
    #    日別テーブルにしか無く、レース別だけ取った回に通知すると
    #    「取り込んでいない日の数字」を報告することになる。
    if want_day and not args.no_notify and start == end:
        notify_sales(start)
    elif not args.no_notify and start != end:
        logger.info("複数日（%s〜%s）のため Discord 通知はしません", start, end)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        logger.error(str(e))
        sys.exit(1)
