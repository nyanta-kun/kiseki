"""
kiseki Windows Agent - JV-Link SDK データ取得・リアルタイム通知

JRA-VAN Data Lab SDKを直接操作し、以下を行う:
1. 蓄積系データ取得（出馬表・成績・血統・調教）→ Mac側FastAPIへPOST
2. 速報系オッズ取得（全券種）→ Mac側FastAPIへPOST
3. リアルタイム通知（出走取消・騎手変更）→ Mac側FastAPIへPOST

ローカルキャッシュ機能:
- JVRead後すぐにローカルJSONLファイルへ保存
- 同一キーのデータがキャッシュ済みならJVOpenをスキップ
- POST失敗分はペンディングキューへ保存し、次回起動時に自動リトライ

動作環境:
- Windows 10/11 (Parallels上でも可)
- Python 3.x 32bit版 (JV-Linkが32bit COMのため必須)
- pywin32
- JV-Linkインストール済み + 利用キー設定済み

使い方:
  python jvlink_agent.py                   # 全機能起動（蓄積取得+リアルタイム監視）
  python jvlink_agent.py --mode setup      # 初回セットアップ（過去データ一括取得）
  python jvlink_agent.py --mode daily      # 当日データ取得のみ
  python jvlink_agent.py --mode realtime   # リアルタイム監視のみ
  python jvlink_agent.py --mode retry      # ペンディングキューのリトライのみ
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# 環境変数読み込み
from dotenv import load_dotenv

from link_common import (
    _normalize_jvread,
    post_to_backend,
    _post_in_batches,
    save_cache,
    load_cache,
    save_pending,
    load_pending_all,
    retry_pending,
    report_status as _lc_report_status,
)

# .envはプロジェクトルートにある想定
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

JRAVAN_SID = os.getenv("JRAVAN_SID", "")
# JRAVAN_SID_2: 蓄積系（setup/recent/daily）専用の第2利用キー。
# 設定すると realtimeはSID1固定、蓄積系はSID2固定で同時実行可能。
# 未設定の場合は全モードでSID1（JRAVAN_SID）を使用する。
JRAVAN_SID_2 = os.getenv("JRAVAN_SID_2", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://YuichironoMacBook-Pro-6.local:8000")
API_KEY = os.getenv("CHANGE_NOTIFY_API_KEY", "")

# ローカルデータディレクトリ
DATA_DIR = Path(__file__).resolve().parent / "data"
CACHE_DIR = DATA_DIR / "cache"       # JVRead生データキャッシュ
PENDING_DIR = DATA_DIR / "pending"   # POST失敗ペンディングキュー
COMPLETED_DIR = DATA_DIR / "completed"  # ファイル単位の処理完了ログ

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("jvlink_agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("jvlink_agent")

# JV-Link データ種別ID
# 蓄積系 (JVOpen)
DATASPEC_RACE = "RACE"   # レース情報(RA) + 馬毎レース情報(SE)
DATASPEC_TOKU = "TOKU"   # 特別登録馬
DATASPEC_DIFF = "TOKU"   # 出馬表(特別登録馬) ※"DIFF"は無効なDataSpec
DATASPEC_BLOD = "BLOD"   # 血統
DATASPEC_MING = "MING"   # 馬名意味由来
DATASPEC_SNAP = "SNAP"   # 調教データ
DATASPEC_SLOP = "SLOP"   # 坂路調教
DATASPEC_YSCH = "YSCH"   # 予定スケジュール
DATASPEC_HOSE = "HOSE"   # 馬基本データ
DATASPEC_HOYU = "HOYU"   # 馬主データ
DATASPEC_WOOD = "WOOD"   # ウッドチップ調教

# 速報系 (JVRTOpen)
# ※ key の形式: 0B11/0B12 は YYYYMMDDJJRR（JVWatchEvent から取得）
# ※              0B31〜0B36 は YYYYMMDDJJKKHHRR（レースキー16文字）
# ※              0B14/0B15/0B16 は YYYYMMDD（開催日）
# ※ 0B31 を日付キー（YYYYMMDD）で呼ぶと rc=-114（key パラメータ不正）
RT_RACE_INFO = "0B12"    # 速報成績（払戻確定後）
RT_ODDS_WIN_PLACE = "0B31"  # 速報オッズ（単複枠）key=レースキー16文字
RT_WEIGHT = "0B11"       # 速報馬体重 key=YYYYMMDDJJRR（JVWatchEvent経由）
RT_SCRATCH = "0B15"      # 速報レース情報（出走取消・騎手変更等）key=YYYYMMDD

# レコード種別IDとデータ内容の対応
RECORD_TYPES = {
    "RA": "レース情報",
    "SE": "馬毎レース情報",
    "HR": "払戻情報",
    "O1": "単勝オッズ",
    "O2": "複勝オッズ",
    "O3": "枠連オッズ",
    "O4": "馬連オッズ",
    "O5": "ワイドオッズ",
    "O6": "馬単オッズ",
    "O7": "三連複オッズ",
    "O8": "三連単オッズ",
    "WE": "馬体重",
    "AV": "出走取消",
    "JC": "騎手変更",
    "CC": "コース変更",
    "WH": "天候馬場変更",
}


# ---------------------------------------------------------------------------
# ローカルキャッシュ（実装は link_common.py に移動）
# ---------------------------------------------------------------------------
# save_cache / load_cache は link_common からインポート済み。
# 各呼び出し箇所では cache_dir=CACHE_DIR を引数として渡す。


# ---------------------------------------------------------------------------
# ペンディングキュー（実装は link_common.py に移動）
# ---------------------------------------------------------------------------
# save_pending / load_pending_all は link_common からインポート済み。
# 各呼び出し箇所では pending_dir=PENDING_DIR を引数として渡す。


# ---------------------------------------------------------------------------
# ファイル単位の処理完了ログ
# ---------------------------------------------------------------------------

def _completed_path(dataspec: str) -> Path:
    return COMPLETED_DIR / f"{dataspec}_completed.txt"


def load_completed_files(dataspec: str) -> set:
    """処理済みファイル名のセットを返す。"""
    path = _completed_path(dataspec)
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def mark_file_completed(dataspec: str, filename: str) -> None:
    """ファイルを処理済みとして記録する。"""
    COMPLETED_DIR.mkdir(parents=True, exist_ok=True)
    with _completed_path(dataspec).open("a", encoding="utf-8") as f:
        f.write(filename + "\n")


# ---------------------------------------------------------------------------
# ペンディングリトライ / JVRead バッファ正規化 / バックエンドPOST（実装は link_common.py に移動）
# ---------------------------------------------------------------------------
# retry_pending / _normalize_jvread / post_to_backend は link_common からインポート済み。
# retry_pending の呼び出し箇所では pending_dir=PENDING_DIR, backend_url=BACKEND_URL,
# api_key=API_KEY を引数として渡す。

# ---------------------------------------------------------------------------
# JV-Link 初期化
# ---------------------------------------------------------------------------

def init_jvlink(sid: str | None = None):
    """JV-Link COMオブジェクトを初期化する。

    Args:
        sid: 利用キー（Noneの場合はグローバルのJRAVAN_SIDを使用）
    """
    use_sid = sid if sid else JRAVAN_SID
    try:
        import win32com.client
        jv = win32com.client.Dispatch("JVDTLab.JVLink")
        # セットアップダイアログ・バルーン通知を非表示にする
        try:
            jv.JVSetUIProperties(False, False)
        except Exception:
            pass  # SDK バージョンによっては未対応でも問題なし
        rc = jv.JVInit(use_sid)
        if rc != 0:
            logger.error(f"JVInit failed: rc={rc}")
            sys.exit(1)
        sid_label = "SID2(蓄積系)" if (JRAVAN_SID_2 and use_sid == JRAVAN_SID_2) else "SID1(共通)"
        logger.info(f"JV-Link initialized successfully ({sid_label})")
        return jv
    except Exception as e:
        logger.error(f"JV-Link initialization error: {e}")
        logger.error("Python 32bit版で実行していますか？ JV-Linkはインストール済みですか？")
        sys.exit(1)


# ---------------------------------------------------------------------------
# JV-Link データ取得（キャッシュ優先）
# ---------------------------------------------------------------------------

def fetch_stored_data(
    jv,
    dataspec: str,
    from_time: str,
    option: int = 1,
    on_file_done=None,
    skip_file_fn=None,
    skip_cache: bool = False,
) -> list[dict]:
    """
    蓄積系データを取得する (JVOpen)。

    キャッシュが存在する場合はJVOpenをスキップしてキャッシュから返す。
    取得成功後はローカルキャッシュへ保存する。

    Args:
        jv: JV-Link COMオブジェクト
        dataspec: データ種別ID (例: "RACE", "DIFF")
        from_time: 取得開始日時 "YYYYMMDDhhmmss"
        option: 1=通常, 2=今週, 3=セットアップ
        on_file_done: ファイル1本読み込み完了時に呼ばれるコールバック
                      signature: on_file_done(filename: str, records: list[dict])
                      これを使うことでファイル単位の即時DB反映が可能
        skip_file_fn: ファイル名を受け取りTrueを返すとJVSkipでスキップする。
                      Noneの場合はスキップしない。
                      signature: skip_file_fn(filename: str) -> bool
        skip_cache: Trueの場合キャッシュの読み書きをスキップし、全レコードの
                    メモリ蓄積も行わない。on_file_done コールバックで逐次処理する
                    大量データ取得時（recentモード等）に使用する。
    """
    # キャッシュ確認
    if not skip_cache:
        cached = load_cache(dataspec, from_time, option, CACHE_DIR)
        if cached is not None:
            logger.info(f"[cache] キャッシュ使用: {dataspec} from={from_time} opt={option} ({len(cached)} records)")
            return cached

    # キャッシュなし → JVOpenで取得
    # JVOpen は長時間ブロックする場合があるため、別スレッドでハートビートを出力する
    logger.info(f"JVOpen 呼び出し開始: dataspec={dataspec}, from={from_time}, option={option}")
    _jvopen_done = threading.Event()

    def _heartbeat():
        start = time.time()
        while not _jvopen_done.is_set():
            _jvopen_done.wait(timeout=30)
            if not _jvopen_done.is_set():
                elapsed = int(time.time() - start)
                logger.info(f"JVOpen 待機中... 経過={elapsed}秒 (JVOpen がブロッキング中)")

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()

    result = jv.JVOpen(dataspec, from_time, option, 0, 0, "")
    _jvopen_done.set()

    if isinstance(result, tuple):
        rc = result[0]
        read_count_total = result[1] if len(result) > 1 else "?"
        dl_count = result[2] if len(result) > 2 else "?"
        last_ts = result[3] if len(result) > 3 else "?"
        next_ts = result[4] if len(result) > 4 else "?"
        last_file = result[5] if len(result) > 5 else "?"
        logger.info(
            f"JVOpen 戻り値: rc={rc}, 読込ファイル数={read_count_total}, "
            f"DL数={dl_count}, 最終TS={last_ts}, 次TS={next_ts}, 最終ファイル={last_file}"
        )
    else:
        rc = result
        logger.info(f"JVOpen 戻り値: rc={rc}")

    if rc < 0:
        logger.error(f"JVOpen エラー: rc={rc}")
        return []

    logger.info(f"JVRead ループ開始 (rc={rc} ファイル)")

    all_records: list[dict] = []   # 全ファイル分の累積（キャッシュ保存用）
    file_records: list[dict] = []  # 現在ファイル分（コールバック用）
    read_count = 0
    current_file = ""
    skip_current = False  # 現在ファイルをスキップ中（レコード蓄積しない）
    last_log_time = time.time()
    wait_count = 0
    error_count = 0
    MAX_ERRORS = 5  # これを超えたら中断
    session_closed = False  # JVClose 済みフラグ（二重クローズ防止）

    def _flush_file(fname: str) -> None:
        """現在ファイルのレコードをコールバックに渡し、累積リストへ移す。"""
        nonlocal file_records, skip_current
        if fname and on_file_done:
            on_file_done(fname, file_records)
        if not skip_cache:
            all_records.extend(file_records)
        file_records = []
        skip_current = False

    while True:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]

        if ret_code == 0:
            # EOF: 最終ファイルをフラッシュして完了
            logger.info("JVRead: EOF 到達 → 読み込み完了")
            _flush_file(current_file)
            break
        elif ret_code == -1:
            # ファイル切り替わり: 前ファイルを即時フラッシュ
            # JVRead 戻り値: (rc, pszBuf, lSize, pszFileName)
            # r[2]=実際の読み込みバイト数(long), r[3]=ファイル名(BSTR)
            new_file = r[3] if len(r) > 3 else (r[2] if len(r) > 2 else "")
            if current_file:
                logger.info(
                    f"JVRead: ファイル完了 {current_file} "
                    f"({len(file_records)} 件) → 次ファイル: {new_file}"
                )
                _flush_file(current_file)
            current_file = new_file
            # skip_file_fn が True を返すファイルは JVSkip で即スキップを試みる。
            # JVSkip が失敗した場合は skip_current=True でレコード蓄積を抑制する。
            if new_file and skip_file_fn and skip_file_fn(new_file):
                rc_skip = jv.JVSkip()
                if rc_skip == 0:
                    # JVSkip 成功: 次ファイルへ即移動
                    logger.info(f"JVSkip: {new_file} (成功)")
                    if on_file_done:
                        on_file_done(new_file, [])
                    current_file = ""
                    skip_current = False
                else:
                    # JVSkip 失敗 (rc={rc_skip}): レコードは読み捨て
                    logger.debug(f"JVSkip: {new_file} 失敗(rc={rc_skip}) → 読み捨てモード")
                    skip_current = True
            else:
                skip_current = False
            continue
        elif ret_code == -3:
            # ダウンロード中
            wait_count += 1
            now = time.time()
            if now - last_log_time >= 30:
                logger.info(
                    f"JVRead: ダウンロード待機中... "
                    f"(待機回数={wait_count}, 取得済={read_count}件, "
                    f"ファイル={current_file or '未開始'})"
                )
                last_log_time = now
                wait_count = 0
            time.sleep(0.5)
            continue
        elif ret_code < -1:
            logger.error(f"JVRead エラー: rc={ret_code}, ファイル={current_file}")
            error_count += 1
            file_records = []  # 不完全データを破棄

            # エラーファイルを completed としてマーク（次回 option=3 実行時のスキップ用）
            if on_file_done and current_file:
                logger.warning(f"エラーファイル {current_file} を completed としてマーク (エラースキップ)")
                on_file_done(current_file, [])

            if error_count >= MAX_ERRORS:
                logger.error(f"エラーが {MAX_ERRORS} 回以上発生。処理を中断します。")
                jv.JVClose()
                session_closed = True
                break

            # JVClose → エラーファイルの日付翌日から JVOpen(option=3) で再開
            # option=1 はカーソルが同じファイルに戻るため -403 ループになる場合がある。
            # ファイル名先頭の日付(pos 4-11, YYYYMMDD)の翌日を from_time にして
            # option=3 でそのファイルを丸ごとスキップする。
            jv.JVClose()
            session_closed = True
            advance_from = None
            if current_file and len(current_file) >= 12:
                try:
                    import datetime as _dt
                    file_date_str = current_file[4:12]  # "YYYYMMDD" 部分
                    file_date = _dt.datetime.strptime(file_date_str, "%Y%m%d")
                    next_day = file_date + _dt.timedelta(days=1)
                    advance_from = next_day.strftime("%Y%m%d000000")
                except Exception:
                    pass
            if advance_from:
                logger.info(
                    f"JVRead エラー後 JVClose。{current_file} の翌日 ({advance_from}) から "
                    f"option=3 で再開... (エラー {error_count}/{MAX_ERRORS})"
                )
                result2 = jv.JVOpen(dataspec, advance_from, 3, 0, 0, "")
            else:
                logger.info(
                    f"JVRead エラー後 JVClose。option=1 でセッション再開を試みます... "
                    f"(エラー {error_count}/{MAX_ERRORS})"
                )
                result2 = jv.JVOpen(dataspec, from_time, 1, 0, 0, "")
            rc2 = result2[0] if isinstance(result2, tuple) else result2
            if rc2 < 0:
                logger.error(f"JVOpen 再開失敗: rc={rc2}。処理を中断します。")
                break
            logger.info(f"JVOpen 再開成功: rc={rc2}。残りファイルの読み取りを続けます。")
            session_closed = False  # 新しいセッションが開いた
            current_file = ""
            continue
        else:
            read_count += 1
            wait_count = 0
            if not skip_current:
                buff = _normalize_jvread(r[1])
                rec_id = buff[:2]
                file_records.append({"rec_id": rec_id, "data": buff})

            if read_count % 1000 == 0:
                logger.info(f"  ... {read_count} 件読込済 (現在ファイル: {current_file})")

    if not session_closed:
        jv.JVClose()
    logger.info(f"JVOpen 完了: {read_count} 件取得 from {dataspec}")

    records = all_records

    # 取得成功したらキャッシュ保存（skip_cache=True の場合はスキップ）
    if not skip_cache:
        save_cache(dataspec, from_time, option, records, CACHE_DIR)

    return records


def fetch_realtime_data(jv, dataspec: str, key: str) -> list[dict]:
    """
    速報系データを取得する (JVRTOpen)

    Args:
        jv: JV-Link COMオブジェクト
        dataspec: データ種別ID (例: "0B31")
        key: レースキー (例: "20260322")
    """
    rc = jv.JVRTOpen(dataspec, key)
    if rc < 0:
        # rc=-1 はデータなし（未確定レースで 0B12 を試みた場合など）、エラーは debug レベル
        logger.debug(f"JVRTOpen no data: rc={rc}, dataspec={dataspec}, key={key[:12]}")
        return []

    records = []
    while True:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]

        if ret_code == 0:
            break
        elif ret_code == -1:
            continue
        elif ret_code < -1:
            logger.error(f"JVRead error: rc={ret_code}")
            break
        else:
            buff = _normalize_jvread(r[1])
            rec_id = buff[:2]
            records.append({"rec_id": rec_id, "data": buff})

    jv.JVClose()
    return records


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _filter_race_records(records: list[dict]) -> list[dict]:
    """RA/SE/HRレコードのみ抽出する。RACE dataspaceにはJG等も混在するため。"""
    return [r for r in records if r.get("rec_id") in ("RA", "SE", "HR")]


def _split_race_hr(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """RA/SE と HR レコードに分割する。

    Returns:
        (ra_se_records, hr_records)
    """
    ra_se = [r for r in records if r.get("rec_id") in ("RA", "SE")]
    hr = [r for r in records if r.get("rec_id") == "HR"]
    return ra_se, hr


def _post_hr_payouts(hr_records: list[dict]) -> None:
    """HR レコードを parse_hr でパースして /api/import/payouts へ送信する。

    Args:
        hr_records: rec_id="HR" のレコードリスト（{"rec_id": "HR", "data": "..."}）
    """
    if not hr_records:
        return

    try:
        from jvlink_parser import parse_hr  # noqa: PLC0415
    except ImportError:
        logger.warning("jvlink_parser.parse_hr が利用できません。HR レコードをスキップします。")
        return

    parsed = []
    for rec in hr_records:
        result = parse_hr(rec.get("data", ""))
        if result:
            parsed.append(result)

    if not parsed:
        return

    ok = post_to_backend("/api/import/payouts", {"records": parsed}, BACKEND_URL, API_KEY)
    if ok:
        logger.info(f"  POST /api/import/payouts {len(parsed)} 件 -> OK")
    else:
        logger.warning(f"  POST /api/import/payouts {len(parsed)} 件 -> NG (ペンディング保存)")
        save_pending("/api/import/payouts", parsed, PENDING_DIR)


# ---------------------------------------------------------------------------
# 動作モード
# ---------------------------------------------------------------------------

def run_daily_fetch(jv) -> None:
    """当日データ取得（毎朝実行）"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d") + "000000"

    # レース情報(RA) + 出馬表(SE) ※出馬表はRACEデータに含まれる
    # RACE dataspaceにはJG等の非対象レコードも混在するためRA/SEのみ送信
    logger.info("=== レース情報・出馬表取得 ===")

    def on_daily_file_done(filename: str, file_records: list[dict]) -> None:
        filtered = _filter_race_records(file_records)
        ra_se, hr = _split_race_hr(filtered)
        if ra_se:
            logger.info(f"  [{filename}] RA/SE {len(ra_se)} 件 → DB反映")
            _post_in_batches("/api/import/races", ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
        if hr:
            logger.info(f"  [{filename}] HR {len(hr)} 件 → 払戻DB反映")
            _post_hr_payouts(hr)

    records = fetch_stored_data(jv, DATASPEC_RACE, yesterday, option=2, on_file_done=on_daily_file_done)
    logger.info(f"Daily fetch complete: 全体 {len(records)} 件")

    # 翌日（出馬表）と当日の指数を自動算出トリガー
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
    today_str = datetime.now().strftime("%Y%m%d")
    for calc_date in [tomorrow, today_str]:
        try:
            resp = requests.post(
                f"{BACKEND_URL}/api/import/calculate",
                params={"date": calc_date},
                headers={"X-API-Key": API_KEY},
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info(f"指数算出トリガー送信: date={calc_date}")
            else:
                logger.warning(f"指数算出トリガー失敗: date={calc_date} status={resp.status_code}")
        except Exception as e:
            logger.warning(f"指数算出トリガー送信エラー: date={calc_date} error={e}")


def _fetch_today_race_keys(today: str) -> list[str]:
    """バックエンドAPIから本日のレースキー（jravan_race_id）一覧を取得する。"""
    try:
        resp = requests.get(f"{BACKEND_URL}/api/races", params={"date": today}, timeout=5)
        if resp.status_code == 200:
            races = resp.json()
            keys = [r["jravan_race_id"] for r in races if r.get("jravan_race_id")]
            return keys
    except Exception as e:
        logger.warning(f"レースキー取得失敗: {e}")
    return []


def run_odds_prefetch(jv, fetch_date: str | None = None) -> None:
    """指定日（デフォルト: 翌日）の前日発売オッズを取得してバックエンドへ送信する。

    JRA前日発売に対応: 前日9時頃から翌日のレースのオッズが `JVRTOpen("0B31", raceKey16)` で取得可能になる。
    1回実行して終了する（VPS cronから1時間ごとに呼び出す想定）。

    Args:
        jv: JV-Link COMオブジェクト
        fetch_date: 対象日 YYYYMMDD。None の場合は翌日を使用。
    """
    if fetch_date is None:
        fetch_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

    logger.info(f"=== ODDS PREFETCH: {fetch_date} のオッズ取得 ===")

    race_keys = _fetch_today_race_keys(fetch_date)
    if not race_keys:
        logger.info(f"対象レースなし（出馬表未登録 or 開催なし）: date={fetch_date}")
        return

    logger.info(f"レースキー {len(race_keys)} 件: {fetch_date}")

    all_o1 = []
    for race_key in race_keys:
        odds_records = fetch_realtime_data(jv, RT_ODDS_WIN_PLACE, race_key)
        o1 = [r for r in odds_records if r.get("rec_id") == "O1"]
        all_o1.extend(o1)

    if all_o1:
        logger.info(f"オッズ取得: {len(all_o1)}件 (O1) / {len(race_keys)}レース → 送信")
        post_to_backend("/api/import/odds", {
            "date": fetch_date,
            "records": all_o1,
        }, BACKEND_URL, API_KEY)
    else:
        logger.info(f"オッズデータなし（前日発売未開始の可能性）: date={fetch_date}")

    logger.info("=== ODDS PREFETCH 完了 ===")


def run_realtime_monitor(jv) -> None:
    """リアルタイム監視ループ"""
    logger.info("=== Realtime monitor started ===")
    today = datetime.now().strftime("%Y%m%d")

    # 送信済み出走取消キー（JVRTOpenが毎回全件返すため重複防止）
    seen_scratches: set[str] = set()
    # 送信済み成績キー（重複防止）
    seen_results: set[str] = set()

    while True:
        try:
            # 日付をループ内で更新（日をまたいでも正しい日付を使う）
            today = datetime.now().strftime("%Y%m%d")
            # 速報オッズ取得（0B31: レースキー単位）
            # 正しい仕様: JVRTOpen("0B31", raceKey16) でレースごとにO1レコードを取得
            race_keys = _fetch_today_race_keys(today)
            if not race_keys:
                logger.debug("本日のレースキーが取得できませんでした")
            all_o1 = []
            for race_key in race_keys:
                odds_records = fetch_realtime_data(jv, RT_ODDS_WIN_PLACE, race_key)
                o1 = [r for r in odds_records if r.get("rec_id") == "O1"]
                all_o1.extend(o1)
            if all_o1:
                logger.info(f"オッズ取得: {len(all_o1)}件 (O1) / {len(race_keys)}レース")
                post_to_backend("/api/import/odds", {
                    "date": today,
                    "records": all_o1,
                }, BACKEND_URL, API_KEY)

            # 出走取消チェック（重複送信防止）
            scratch_records = fetch_realtime_data(jv, RT_SCRATCH, today)
            new_scratches = []
            for rec in scratch_records:
                key = rec["data"][:40]  # 先頭40文字でユニーク識別
                if key not in seen_scratches:
                    seen_scratches.add(key)
                    new_scratches.append(rec)
            for rec in new_scratches:
                logger.warning(f"出走取消検知: {rec['data'][:30]}")
                post_to_backend("/api/changes/notify", {
                    "change_type": "scratch",
                    "raw_data": rec["data"],
                    "detected_at": datetime.now().isoformat(),
                }, BACKEND_URL, API_KEY)
            if scratch_records and not new_scratches:
                logger.debug(f"出走取消: {len(scratch_records)}件（送信済みスキップ）")

            # 馬体重
            weight_records = fetch_realtime_data(jv, RT_WEIGHT, today)
            if weight_records:
                post_to_backend("/api/import/weights", {
                    "date": today,
                    "records": weight_records,
                }, BACKEND_URL, API_KEY)

            # 速報成績（払戻確定後）: 各レースキーで 0B12 を試行
            # 0B12 の正規キーは YYYYMMDDJJRR だが、16文字レースキーで呼んでも受理される場合がある
            new_results = []
            new_payouts = []
            for race_key in race_keys:
                result_records = fetch_realtime_data(jv, RT_RACE_INFO, race_key)
                for rec in result_records:
                    rec_id = rec.get("rec_id")
                    if rec_id == "SE":
                        key = rec["data"][:30]  # 先頭30文字でユニーク識別
                        if key not in seen_results:
                            seen_results.add(key)
                            new_results.append(rec)
                    elif rec_id == "HR":
                        key = rec["data"][:30]
                        if key not in seen_results:
                            seen_results.add(key)
                            new_payouts.append(rec)
            if new_results:
                logger.info(f"成績取得: {len(new_results)}件 (SE) → /api/import/races へ送信")
                post_to_backend("/api/import/races", {"records": new_results}, BACKEND_URL, API_KEY)
            if new_payouts:
                logger.info(f"払戻取得: {len(new_payouts)}件 (HR) → /api/import/payouts へ送信")
                _post_hr_payouts(new_payouts)

            time.sleep(30)  # 30秒間隔

        except KeyboardInterrupt:
            logger.info("Realtime monitor stopped by user")
            break
        except Exception as e:
            logger.error(f"Realtime monitor error: {e}")
            time.sleep(10)


def run_setup(jv) -> None:
    """初回セットアップ（全期間データ一括取得）

    option=3（セットアップモード）で JRA-VAN の全過去ファイルを取得する。
    from_time は指定するが、option=3 は from_time を無視して全ファイルを返す仕様。
    意図的に全期間（JRA-VAN 提供の最古データから）を取得する。

    ファイル単位の完了ログ（data/completed/）により:
    - 処理済みファイルは再起動時もDBへの二重登録をスキップ
    - 中断後の再起動で未処理ファイルから再開可能
    """
    logger.info("=== SETUP MODE: 全期間データ一括取得 ===")
    logger.info("※ option=3 で JRA-VAN 全過去ファイルを取得します（from_time は無視される）。")
    logger.info("※ ファイル1本完了ごとに即時DBへ反映します。")
    logger.info("※ 処理済みファイルは再起動時にスキップします。")

    # option=3 は from_time を無視して全ファイルを返すが、引数として渡す必要がある
    from_time = "19860101000000"  # JRA-VAN データ提供開始年（形式上の基準日）

    # 処理済みファイルを読み込む（再起動時のスキップ用）
    completed = load_completed_files(DATASPEC_RACE)
    if completed:
        logger.info(f"[completed] 処理済みファイル: {len(completed)} 件（JVSkip対象）")

    total_posted = {"ra_se": 0, "files": 0, "skipped": 0}

    def on_race_file_done(filename: str, file_records: list[dict]) -> None:
        # JVSkip経由でスキップされた場合（file_records が空 かつ completedに未登録）
        if not file_records and filename not in completed:
            mark_file_completed(DATASPEC_RACE, filename)
            total_posted["skipped"] += 1
            return
        # 処理済みファイルはスキップ（JVSkipで来るはずだが念のため）
        if filename in completed:
            total_posted["skipped"] += 1
            return

        filtered = _filter_race_records(file_records)
        ra_se, hr = _split_race_hr(filtered)
        if not ra_se and not hr:
            logger.info(f"  [{filename}] RA/SE/HR なし ({len(file_records)} 件中) → 完了マーク")
            mark_file_completed(DATASPEC_RACE, filename)
            return

        if ra_se:
            logger.info(
                f"  [{filename}] RA/SE {len(ra_se)} 件 / 全 {len(file_records)} 件 → DB反映開始"
            )
            _post_in_batches("/api/import/races", ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
            total_posted["ra_se"] += len(ra_se)
        if hr:
            logger.info(f"  [{filename}] HR {len(hr)} 件 → 払戻DB反映")
            _post_hr_payouts(hr)
        total_posted["files"] += 1
        mark_file_completed(DATASPEC_RACE, filename)
        logger.info(
            f"  [{filename}] 完了 (累計: ファイル {total_posted['files']} 本 / {total_posted['ra_se']} 件)"
        )

    def race_skip_fn(filename: str) -> bool:
        """処理済みファイルは JVSkip でスキップする。"""
        return filename in completed

    # RACE: option=3 で全過去ファイルを取得、ファイル完了ごとに即時DB反映
    # 処理済みファイルは JVSkip で高速スキップ（全レコード読み込みを回避）
    logger.info(f"Fetching RACE from {from_time} (option=3, セットアップモード)...")
    fetch_stored_data(
        jv, DATASPEC_RACE, from_time, option=3,
        on_file_done=on_race_file_done, skip_file_fn=race_skip_fn,
    )
    logger.info(
        f"RACE 取得完了: {total_posted['files']} ファイル / "
        f"{total_posted['ra_se']} 件をDBへ反映 / {total_posted['skipped']} ファイルスキップ"
    )

    # BLOD: 血統データ（HN/SK レコード）を取得して /api/import/bloodlines へ送信
    completed_blod = load_completed_files(DATASPEC_BLOD)
    total_blod = {"hn_sk": 0, "files": 0, "skipped": 0}

    def on_blod_file_done(filename: str, file_records: list[dict]) -> None:
        if filename in completed_blod:
            total_blod["skipped"] += 1
            return
        hn_sk = [r for r in file_records if r.get("rec_id") in ("HN", "SK")]
        if not hn_sk:
            mark_file_completed(DATASPEC_BLOD, filename)
            return
        logger.info(f"  [{filename}] HN/SK {len(hn_sk)} 件 → DB反映開始")
        _post_in_batches("/api/import/bloodlines", hn_sk, 500, BACKEND_URL, API_KEY, PENDING_DIR)
        total_blod["hn_sk"] += len(hn_sk)
        total_blod["files"] += 1
        mark_file_completed(DATASPEC_BLOD, filename)
        logger.info(
            f"  [{filename}] 完了 (累計: ファイル {total_blod['files']} 本 / {total_blod['hn_sk']} 件)"
        )

    logger.info(f"Fetching BLOD from {from_time} (option=3, セットアップモード)...")
    fetch_stored_data(
        jv, DATASPEC_BLOD, from_time, option=3,
        on_file_done=on_blod_file_done,
        skip_file_fn=lambda fn: fn in completed_blod,
    )
    logger.info(
        f"BLOD 取得完了: {total_blod['files']} ファイル / "
        f"{total_blod['hn_sk']} 件をDBへ反映 / {total_blod['skipped']} ファイルスキップ"
    )


def _run_blod_only(jv) -> None:
    """血統データ（BLOD）のみを取得してDBへ送信する。

    run_setup() の BLOD フェーズを独立させたもの。
    RACE フェーズをスキップするため、血統データだけを素早く取得できる。
    """
    logger.info("=== BLOD-ONLY MODE: 血統データ取得 ===")
    from_time = "19860101000000"
    completed_blod = load_completed_files(DATASPEC_BLOD)
    if completed_blod:
        logger.info(f"[completed] 処理済みBLODファイル: {len(completed_blod)} 件（スキップ対象）")

    total_blod = {"hn_sk": 0, "files": 0, "skipped": 0}

    def on_blod_file_done(filename: str, file_records: list[dict]) -> None:
        if filename in completed_blod:
            total_blod["skipped"] += 1
            return
        hn_sk = [r for r in file_records if r.get("rec_id") in ("HN", "SK")]
        if not hn_sk:
            mark_file_completed(DATASPEC_BLOD, filename)
            return
        logger.info(f"  [{filename}] HN/SK {len(hn_sk)} 件 → DB反映開始")
        _post_in_batches("/api/import/bloodlines", hn_sk, 500, BACKEND_URL, API_KEY, PENDING_DIR)
        total_blod["hn_sk"] += len(hn_sk)
        total_blod["files"] += 1
        mark_file_completed(DATASPEC_BLOD, filename)
        logger.info(
            f"  [{filename}] 完了 (累計: ファイル {total_blod['files']} 本 / {total_blod['hn_sk']} 件)"
        )

    logger.info(f"Fetching BLOD from {from_time} (option=3)...")
    fetch_stored_data(
        jv, DATASPEC_BLOD, from_time, option=3,
        on_file_done=on_blod_file_done,
        skip_file_fn=lambda fn: fn in completed_blod,
    )
    logger.info(
        f"BLOD 取得完了: {total_blod['files']} ファイル / "
        f"{total_blod['hn_sk']} 件をDBへ反映 / {total_blod['skipped']} ファイルスキップ"
    )


def run_recent(jv, from_year: int = 2023) -> None:
    """直近データを取得する（option=2: 今週分のファイルのみ）。

    【重要】option=3 は from_time を無視して JRA-VAN 全過去ファイルをスキャンするため、
    JVOpen が数時間〜十数時間ブロックする。再発防止のため option=2 を使用する。

    option=2（今週）: JVOpen が数十秒で完了する。直近の成績・出馬表の修復に使用。
    - 数週間以上前のデータ修復には option=2 では取得できない場合がある
    - 全期間の再取得が必要な場合は --mode setup を使用すること

    Args:
        jv: JV-Link COMオブジェクト
        from_year: 取得開始年（option=2 では今週分が返るため実質的には無視される）
    """
    from_time = f"{from_year}0101000000"
    logger.info(f"=== RECENT MODE: 直近データ取得 (option=2, from={from_time} ※今週分が対象) ===")

    completed = load_completed_files(DATASPEC_RACE)
    if completed:
        logger.info(f"[completed] 処理済みファイル: {len(completed)} 件（JVSkip対象）")

    total_posted = {"ra_se": 0, "files": 0, "skipped": 0}

    def on_race_file_done(filename: str, file_records: list[dict]) -> None:
        # JVSkip経由でスキップされた場合（file_records が空 かつ completedに未登録）
        if not file_records and filename not in completed:
            mark_file_completed(DATASPEC_RACE, filename)
            completed.add(filename)  # メモリ内セットも更新（エラーリトライ時のJVSkip用）
            total_posted["skipped"] += 1
            return
        # 処理済みファイルはスキップ
        if filename in completed:
            total_posted["skipped"] += 1
            return
        filtered = _filter_race_records(file_records)
        ra_se, hr = _split_race_hr(filtered)
        if not ra_se and not hr:
            logger.info(f"  [{filename}] RA/SE/HR なし ({len(file_records)} 件中) → 完了マーク")
            mark_file_completed(DATASPEC_RACE, filename)
            completed.add(filename)
            return
        if ra_se:
            logger.info(
                f"  [{filename}] RA/SE {len(ra_se)} 件 / 全 {len(file_records)} 件 → DB反映開始"
            )
            _post_in_batches("/api/import/races", ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
            total_posted["ra_se"] += len(ra_se)
        if hr:
            logger.info(f"  [{filename}] HR {len(hr)} 件 → 払戻DB反映")
            _post_hr_payouts(hr)
        total_posted["files"] += 1
        mark_file_completed(DATASPEC_RACE, filename)
        completed.add(filename)
        logger.info(
            f"  [{filename}] 完了 (累計: ファイル {total_posted['files']} 本 / {total_posted['ra_se']} 件)"
        )

    def recent_skip_fn(filename: str) -> bool:
        """処理済みファイルのみ JVSkip でスキップ。"""
        return filename in completed

    logger.info(f"Fetching RACE (option=2, from={from_time})...")
    fetch_stored_data(
        jv, DATASPEC_RACE, from_time, option=2,
        on_file_done=on_race_file_done, skip_file_fn=recent_skip_fn,
        skip_cache=True,
    )
    logger.info(
        f"RECENT 完了: {total_posted['files']} ファイル / "
        f"{total_posted['ra_se']} 件をDBへ反映 / {total_posted['skipped']} ファイルスキップ"
    )


# ---------------------------------------------------------------------------
# コマンドポーリング / ステータス報告
# ---------------------------------------------------------------------------


def report_status(
    status: str,
    mode: str | None = None,
    message: str = "",
    progress: dict | None = None,
) -> None:
    """グローバル変数を補完する report_status のローカルラッパー。

    link_common.report_status を BACKEND_URL / API_KEY で呼び出す。

    Args:
        status: "running" | "idle" | "error" | "done"
        mode: "setup" | "daily" | "realtime" | None
        message: 状態の説明
        progress: 任意の進捗情報
    """
    _lc_report_status(status, mode, message, progress, BACKEND_URL, API_KEY)


def poll_command() -> dict | None:
    """BackendからMac側が送信したコマンドを取得する。

    Returns:
        コマンド dict（例: {"action": "setup"}）、なければ None
    """
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/agent/command",
            headers={"X-API-Key": API_KEY},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("action"):
                return data
    except Exception as e:
        logger.debug(f"Command poll failed (non-critical): {e}")
    return None


def run_command_loop(jv) -> None:
    """コマンドキューをポーリングし続け、コマンドを実行する。

    Backendの /api/agent/command を定期ポーリング（30秒間隔）し、
    Mac側からのコマンドを受け取って実行する。
    """
    logger.info("=== COMMAND LOOP MODE: Backendのコマンドをポーリング中 ===")
    report_status("idle", message="Waiting for commands from Mac")

    while True:
        try:
            cmd = poll_command()
            if cmd:
                action = cmd.get("action")
                logger.info(f"[command] 受信: action={action} params={cmd.get('params', {})}")

                if action == "setup":
                    report_status("running", mode="setup", message="Starting setup mode (JVOpen option=3)")
                    run_setup(jv)
                    report_status("idle", message="Setup completed")
                elif action == "daily":
                    report_status("running", mode="daily", message="Starting daily fetch")
                    run_daily_fetch(jv)  # 内部で指数算出トリガーも送信
                    report_status("idle", message="Daily fetch and index calculation triggered")
                elif action == "odds_prefetch":
                    fetch_date = cmd.get("params", {}).get("date")
                    report_status("running", mode="odds_prefetch", message=f"Fetching prefetch odds: {fetch_date or 'tomorrow'}")
                    run_odds_prefetch(jv, fetch_date)
                    report_status("idle", message=f"Odds prefetch done: {fetch_date or 'tomorrow'}")
                elif action == "retry":
                    report_status("running", mode="retry", message="Retrying pending queue")
                    retry_pending(PENDING_DIR, BACKEND_URL, API_KEY)
                    report_status("idle", message="Retry completed")
                elif action == "recent":
                    from_year = cmd.get("params", {}).get("from_year", 2023)
                    year_month = cmd.get("params", {}).get("year_month", "")
                    report_status(
                        "running",
                        mode="recent",
                        message=f"Starting recent mode (from {from_year}, year_month={year_month})",
                    )
                    run_recent(jv, from_year=from_year)
                    report_status("idle", message=f"Recent fetch completed (from_year={from_year})")
                elif action == "stop":
                    report_status("done", message="Stopped by command from Mac")
                    logger.info("[command] stop受信 → 終了")
                    break
                else:
                    logger.warning(f"[command] 未知のaction: {action}")

            time.sleep(30)

        except KeyboardInterrupt:
            report_status("done", message="Stopped by user (KeyboardInterrupt)")
            logger.info("Command loop stopped by user")
            break
        except Exception as e:
            logger.error(f"Command loop error: {e}")
            report_status("error", message=str(e))
            time.sleep(30)


def main() -> None:
    """エントリーポイント"""
    parser = argparse.ArgumentParser(description="kiseki JV-Link Agent")
    parser.add_argument(
        "--mode",
        choices=["all", "setup", "blod", "recent", "daily", "realtime", "odds-prefetch", "retry", "wait"],
        default="all",
        help="動作モード (default: all, blod=血統データのみ取得, odds-prefetch=前日発売オッズ取得, wait=コマンド待ち受けモード)",
    )
    parser.add_argument(
        "--fetch-date",
        type=str,
        default=None,
        metavar="YYYYMMDD",
        help="odds-prefetch モードで取得する対象日 (default: 翌日, 例: --fetch-date 20260406)",
    )
    parser.add_argument(
        "--from-year",
        type=int,
        default=2023,
        metavar="YEAR",
        help="recent モードで取得する開始年 (default: 2023, 例: --from-year 2020)",
    )
    args = parser.parse_args()

    if not JRAVAN_SID and args.mode not in ("retry", "wait"):
        logger.error("JRAVAN_SID が設定されていません。.env を確認してください。")
        sys.exit(1)

    # デュアルSID構成: JRAVAN_SID_2が設定されている場合
    # - realtime: SID1（常時接続維持）
    # - setup/recent/daily/blod/odds-prefetch: SID2（蓄積系専用）
    # SID2未設定の場合は全モードでSID1を使用（従来通り）
    BULK_MODES = ("setup", "recent", "daily", "blod", "odds-prefetch", "all")
    use_sid = JRAVAN_SID_2 if (JRAVAN_SID_2 and args.mode in BULK_MODES) else JRAVAN_SID
    if JRAVAN_SID_2:
        sid_role = "SID2(蓄積系専用)" if args.mode in BULK_MODES else "SID1(realtime専用)"
        logger.info(f"デュアルSID構成: {sid_role} を使用")

    logger.info(f"kiseki JV-Link Agent starting (mode={args.mode})")
    logger.info(f"Backend URL: {BACKEND_URL}")
    logger.info(f"Data dir: {DATA_DIR}")

    # 起動時に常にペンディングリトライ（retryモード以外でも）
    retry_pending(PENDING_DIR, BACKEND_URL, API_KEY)

    if args.mode == "retry":
        # リトライのみで終了
        return

    if args.mode == "wait":
        # JV-Linkなしでコマンド待ち受けのみ（デバッグ・テスト用）
        jv = None
        try:
            jv = init_jvlink(use_sid)
        except SystemExit:
            logger.warning("JV-Link 初期化失敗。コマンド受信は可能ですが実行はできません。")
        run_command_loop(jv)
        return

    jv = init_jvlink(use_sid)

    if args.mode == "setup":
        report_status("running", mode="setup", message="Starting setup mode")
        run_setup(jv)
        report_status("idle", message="Setup completed. Entering command loop.")
        run_command_loop(jv)
    elif args.mode == "blod":
        report_status("running", mode="blod", message="Starting BLOD-only fetch")
        _run_blod_only(jv)
        report_status("done", message="BLOD fetch completed.")
        jv.JVClose()
        logger.info("blod モード完了。終了します。")
    elif args.mode == "recent":
        report_status("running", mode="recent", message=f"Starting recent mode ({args.from_year}+)")
        run_recent(jv, from_year=args.from_year)
        report_status("done", message="Recent mode completed.")
        jv.JVClose()
        logger.info("recent モード完了。終了します。")
    elif args.mode == "daily":
        report_status("running", mode="daily", message="Starting daily fetch")
        run_daily_fetch(jv)
        report_status("idle", message="Daily fetch completed. Entering command loop.")
        run_command_loop(jv)
    elif args.mode == "realtime":
        run_realtime_monitor(jv)
    elif args.mode == "odds-prefetch":
        report_status("running", mode="odds_prefetch", message=f"Starting odds prefetch: {args.fetch_date or 'tomorrow'}")
        run_odds_prefetch(jv, args.fetch_date)
        report_status("done", message=f"Odds prefetch completed: {args.fetch_date or 'tomorrow'}")
        jv.JVClose()
        logger.info("odds-prefetch モード完了。終了します。")
    elif args.mode == "all":
        run_daily_fetch(jv)
        report_status("idle", message="Daily fetch done. Entering command loop + realtime.")
        # コマンドループをバックグラウンドスレッドで起動
        cmd_thread = threading.Thread(target=run_command_loop, args=(jv,), daemon=True)
        cmd_thread.start()
        run_realtime_monitor(jv)


if __name__ == "__main__":
    main()
