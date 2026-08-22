"""
kiseki JV-Link Historical Data Fetcher

2000年以降の全RACEデータ・馬マスタデータを完全取得するスクリプト。
- completedファイルによる再開可能処理（中断後に続きから再開）
- 時間制限付き（--time-limit）でTask Schedulerと組み合わせて断続的に処理
- ファイル単位の完了記録でDBへの二重登録を防止
- 多重起動防止（ロックファイル）

仕組み:
  1. JVOpen(RACE, option=1, from="20000101") で全過去ファイルの一覧を取得（数分で完了）
  2. completed ファイルに記録済みのものはJVSkipで高速スキップ
  3. 未処理ファイルのみ JVRead → DB反映 → completed記録
  4. time_limit 秒経過後はファイル完了単位でgraceful stop
  5. Task Schedulerが次回実行時に続きから再開

使い方:
  python jvlink_historical.py                    # race + horses を順次取得
  python jvlink_historical.py --mode race        # RACEデータのみ
  python jvlink_historical.py --mode horses      # UM（競走馬マスタ）のみ
  python jvlink_historical.py --time-limit 7200  # 2時間制限（デフォルト）
  python jvlink_historical.py --from-date 20000101  # 2000年以降（デフォルト）
  python jvlink_historical.py --status           # 進捗表示のみ

RunAdhoc経由での起動（推奨）:
  ssh windows-vm "echo jvlink_historical.py --mode all > C:\\kiseki\\windows-agent\\adhoc_cmd.txt && schtasks /run /tn kiseki-RunAdhoc"
"""

import argparse
import ctypes
import ctypes.wintypes
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

# WindowsシステムCA証明書をPython SSLに注入（Let's Encrypt E8等の新CAに対応）
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import jvlink_maintenance as jvm
from link_common import (
    BlockingCallGuard,
    _normalize_jvread,
    _post_in_batches,
    post_to_backend,
    retry_pending,
    rotating_log_handler,
    save_pending,
)

# .env読み込み（プロジェクトルートの .env）
env_path = _SCRIPT_DIR.parent / ".env"
load_dotenv(env_path)

JRAVAN_SID = os.getenv("JRAVAN_SID", "")
JRAVAN_SID_2 = os.getenv("JRAVAN_SID_2", "")  # 蓄積系専用SIDがあればそちらを使用
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("CHANGE_NOTIFY_API_KEY", "")

DATA_DIR = _SCRIPT_DIR / "data"
PENDING_DIR = DATA_DIR / "pending"
COMPLETED_DIR = DATA_DIR / "completed"
LOCK_FILE = DATA_DIR / "historical_running.lock"

# ログ設定: コンソール + ファイル両方に出力
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        rotating_log_handler(_SCRIPT_DIR / "historical_agent.log"),
    ],
)
logger = logging.getLogger("historical")

# JVOpen がこの秒数を超えて返らなければハングとみなしプロセスごと落とす。
# option=1（差分）は CLAUDE.md の指針どおり数分で完了する想定なので 1 時間で十分。
# option=4（セットアップ）は数時間ブロックしうるため個別に緩める（_fetch_with_stop 参照）。
JVOPEN_TIMEOUT_DIFF = int(os.getenv("JVOPEN_TIMEOUT_DIFF", "3600"))
JVOPEN_TIMEOUT_SETUP = int(os.getenv("JVOPEN_TIMEOUT_SETUP", "21600"))

# completed ファイルキー
# RACE は jvlink_agent.py の "RACE" キーと共有（両スクリプト間でスキップが有効）
COMPLETED_KEY_RACE = "RACE"
# UM（競走馬マスタ）は blod-um モードと同じキーを使用（処理済みを引き継ぐ）
COMPLETED_KEY_HORSES = "BLOD_UM"


# ---------------------------------------------------------------------------
# completed ファイル管理
# ---------------------------------------------------------------------------

def _completed_path(key: str) -> Path:
    return COMPLETED_DIR / f"{key}_completed.txt"


def load_completed_files(key: str) -> set:
    """処理済みファイル名のセットを返す。"""
    path = _completed_path(key)
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def mark_file_completed(key: str, filename: str, completed_set: set) -> None:
    """ファイルを処理済みとしてファイルとメモリ両方に記録する。"""
    COMPLETED_DIR.mkdir(parents=True, exist_ok=True)
    with _completed_path(key).open("a", encoding="utf-8") as f:
        f.write(filename + "\n")
    completed_set.add(filename)


# ---------------------------------------------------------------------------
# 多重起動防止（ロックファイル）
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """Windows API で PID が生存しているか確認する。"""
    try:
        PROCESS_QUERY_INFORMATION = 0x0400
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(handle)
        STILL_ACTIVE = 259
        return exit_code.value == STILL_ACTIVE
    except Exception:
        return False


def check_already_running() -> bool:
    """ロックファイルで多重起動を防ぐ。stale lock (3時間超) は自動削除。"""
    if not LOCK_FILE.exists():
        return False
    try:
        parts = LOCK_FILE.read_text(encoding="utf-8").strip().split()
        pid = int(parts[0])
        created_at = datetime.fromisoformat(parts[1]) if len(parts) > 1 else datetime.min
        # time_limit=7200 の3倍 = 6時間で stale とみなす
        if (datetime.now() - created_at).total_seconds() > 21600:
            logger.info(f"[lock] stale lock (作成={created_at}, pid={pid}) を削除")
            LOCK_FILE.unlink()
            return False
        if not _is_pid_alive(pid):
            logger.info(f"[lock] pid={pid} は終了済み。lock を削除します。")
            LOCK_FILE.unlink()
            return False
        logger.info(f"[lock] pid={pid} が実行中 (作成={created_at})")
        return True
    except Exception as e:
        logger.warning(f"[lock] 読み取りエラー ({e})。lock を削除します。")
        LOCK_FILE.unlink(missing_ok=True)
        return False


def write_lock() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(f"{os.getpid()} {datetime.now().isoformat()}", encoding="utf-8")


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# JV-Link 初期化
# ---------------------------------------------------------------------------

def init_jvlink():
    """JV-Link COMオブジェクトを初期化する。蓄積系用SIDを優先使用。"""
    use_sid = JRAVAN_SID_2 if JRAVAN_SID_2 else JRAVAN_SID
    try:
        import win32com.client
        jv = win32com.client.Dispatch("JVDTLab.JVLink")
        rc = jv.JVInit(use_sid)
        if rc != 0:
            logger.error(f"JVInit failed: rc={rc}")
            sys.exit(1)
        sid_label = "SID2(蓄積系専用)" if JRAVAN_SID_2 else "SID1"
        logger.info(f"JV-Link initialized (historical, {sid_label})")
        return jv
    except Exception as e:
        logger.error(f"JV-Link initialization error: {e}")
        logger.error("Python 32bit版で実行していますか？ JV-Linkはインストール済みですか？")
        sys.exit(1)


# ---------------------------------------------------------------------------
# HR（払戻）レコード送信
# ---------------------------------------------------------------------------

def post_hr_payouts(hr_records: list[dict]) -> None:
    """HR レコードを parse_hr でパースして /api/import/payouts へ送信する。"""
    if not hr_records:
        return
    try:
        from jvlink_parser import parse_hr  # noqa: PLC0415
    except ImportError:
        logger.warning("jvlink_parser が見つかりません。HR レコードをスキップします。")
        return
    parsed = [parse_hr(r.get("data", "")) for r in hr_records]
    parsed = [p for p in parsed if p]
    if not parsed:
        return
    ok = post_to_backend("/api/import/payouts", {"records": parsed}, BACKEND_URL, API_KEY)
    if ok:
        logger.info(f"  POST /api/import/payouts {len(parsed)} 件 -> OK")
    else:
        logger.warning(f"  POST /api/import/payouts {len(parsed)} 件 -> NG (pending)")
        save_pending("/api/import/payouts", parsed, PENDING_DIR)


# ---------------------------------------------------------------------------
# JVOpen + JVRead ループ（時間制限対応）
# ---------------------------------------------------------------------------

def _fetch_with_stop(
    jv,
    dataspec: str,
    from_time: str,
    option: int,
    on_file_done,
    skip_file_fn,
    stop_event: threading.Event,
    max_errors: int = 10,
) -> bool:
    """
    JVOpen + JVRead ループ。stop_event がセットされたらファイル完了後に graceful stop。

    Args:
        jv: JV-Link COMオブジェクト
        dataspec: DataSpec ID ("RACE", "DIFN" 等)
        from_time: 取得開始日時 "YYYYMMDDhhmmss"
        option: JVOpen オプション (1=差分, 4=セットアップ)
        on_file_done: ファイル完了コールバック (filename, records) -> None
        skip_file_fn: スキップ判定 filename -> bool (JVSkip で高速スキップ)
        stop_event: セットされるとファイル完了後に停止
        max_errors: JVRead エラー許容回数

    Returns:
        True=全ファイル処理完了, False=時間制限で中断
    """
    # メンテナンス窓中は JVOpen を呼ばない。呼ぶと JV-Link がモーダルダイアログを
    # 出し、pythonw.exe には閉じる者がいないため COM が数十分ブロックしたうえで
    # rc=-504 を返す（2026-08-04: 1193 秒待たされ time_limit を食い潰した）。
    if reason := jvm.skip_reason(f"JVOpen({dataspec})", logger=logger):
        logger.warning(reason)
        raise jvm.MaintenanceWindowActive(reason)

    logger.info(f"JVOpen 開始: dataspec={dataspec}, from_time={from_time}, option={option}")

    # JVOpen はブロッキングのため、別スレッドで経過を記録しつつ上限を監視する。
    # 上限で落とすのは、JV-Link が同時1接続しか持てず、ここで居座ると
    # 他のバックフィルも realtime も全て巻き添えになるため（BlockingCallGuard 参照）。
    timeout = JVOPEN_TIMEOUT_SETUP if option == 4 else JVOPEN_TIMEOUT_DIFF
    with BlockingCallGuard(f"JVOpen({dataspec})", timeout, logger):
        result = jv.JVOpen(dataspec, from_time, option, 0, 0, "")

    if isinstance(result, tuple):
        rc = result[0]
        file_count = result[1] if len(result) > 1 else "?"
        dl_count = result[2] if len(result) > 2 else "?"
        logger.info(f"JVOpen 完了: rc={rc}, ファイル数={file_count}, DL数={dl_count}")
    else:
        rc = result
        logger.info(f"JVOpen 完了: rc={rc}")

    if rc < 0:
        kind = jvm.log_open_failure(logger, f"JVOpen({dataspec})", rc)
        if kind == jvm.RC_MAINTENANCE:
            # 窓の外でメンテナンスに当たった（臨時メンテ・窓の設定漏れ）。
            # 後続の dataspec も確実に同じ結果になるので、その回は丸ごと見送る。
            raise jvm.MaintenanceWindowActive(jvm.describe_rc(rc))
        return False

    file_records: list[dict] = []
    current_file = ""
    skip_current = False
    read_count = 0
    error_count = 0
    session_closed = False
    last_log_time = time.time()
    last_progress_time = time.time()
    all_completed = True  # EOF 到達で True、time_limit 中断で False

    def _flush_file(fname: str) -> None:
        nonlocal file_records, skip_current
        if fname and on_file_done:
            on_file_done(fname, file_records)
        file_records = []
        skip_current = False

    logger.info("JVRead ループ開始...")

    while True:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]

        if ret_code == 0:
            # EOF: 全ファイル取得完了
            logger.info(f"JVRead: EOF 到達 → 全ファイル処理完了 ({read_count} レコード)")
            _flush_file(current_file)
            break

        elif ret_code == -1:
            # ファイル切り替わり
            new_file = r[3] if len(r) > 3 else (r[2] if len(r) > 2 else "")
            if current_file:
                logger.debug(
                    f"ファイル完了: {current_file} ({len(file_records)} 件) → {new_file}"
                )
                _flush_file(current_file)
                # graceful stop チェック（ファイル完了単位で停止）
                if stop_event.is_set():
                    logger.info(
                        f"[time_limit] {current_file} 完了後に中断します。"
                        f" (読込済={read_count} 件)"
                    )
                    all_completed = False
                    if not session_closed:
                        jv.JVClose()
                        session_closed = True
                    break

            current_file = new_file

            # 処理済みファイルは JVSkip で高速スキップ
            if new_file and skip_file_fn and skip_file_fn(new_file):
                # JVSkip の戻り値は VT_VOID（4.9 仕様書・5.0.0 の型情報とも「戻り値なし」）。
                # pywin32 は None を返すため、以前の `if rc_skip == 0:` は必ず False になり、
                # 4 箇所すべてが恒常的に「JVSkip 失敗 → 読み捨てモード」に落ちていた。
                # 戻り値は見ず、無条件に成功として扱う（2026-08-23 修正）。
                jv.JVSkip()
                logger.debug(f"JVSkip: {new_file}")
                if on_file_done:
                    on_file_done(new_file, [])  # スキップ通知
                current_file = ""
                skip_current = False
            else:
                skip_current = False
            continue

        elif ret_code == -3:
            # ダウンロード待機中
            now = time.time()
            if now - last_log_time >= 30:
                logger.info(
                    f"ダウンロード待機中... (取得済={read_count} 件, ファイル={current_file or '未開始'})"
                )
                last_log_time = now
            time.sleep(0.5)
            continue

        elif ret_code < -1:
            # JVRead エラー
            logger.error(f"JVRead エラー: rc={ret_code}, ファイル={current_file}")
            error_count += 1
            file_records = []

            # エラーファイルを completed マーク（再処理でまた失敗しないよう）
            if on_file_done and current_file:
                logger.warning(f"エラーファイル {current_file} を completed にマーク")
                on_file_done(current_file, [])

            if error_count >= max_errors:
                logger.error(f"エラーが {max_errors} 回に達しました。処理を中断します。")
                jv.JVClose()
                session_closed = True
                all_completed = False
                break

            # エラーファイルの翌日から JVOpen(option=1) で再開
            jv.JVClose()
            session_closed = True
            advance_from = from_time
            if current_file and len(current_file) >= 12:
                try:
                    file_date = datetime.strptime(current_file[4:12], "%Y%m%d")
                    advance_from = (file_date + timedelta(days=1)).strftime("%Y%m%d000000")
                except Exception:
                    pass
            logger.info(f"  JVOpen 再開: from={advance_from}, option=1 (エラー {error_count}/{max_errors})")
            result2 = jv.JVOpen(dataspec, advance_from, 1, 0, 0, "")
            rc2 = result2[0] if isinstance(result2, tuple) else result2
            if rc2 < 0:
                kind2 = jvm.log_open_failure(logger, f"JVOpen({dataspec}) 再開", rc2)
                if kind2 == jvm.RC_MAINTENANCE:
                    raise jvm.MaintenanceWindowActive(jvm.describe_rc(rc2))
                logger.error("JVOpen 再開失敗。処理を中断します。")
                all_completed = False
                break
            logger.info(f"  JVOpen 再開成功: rc={rc2}")
            session_closed = False
            current_file = ""
            continue

        else:
            # データレコード
            read_count += 1
            if not skip_current:
                raw = r[1]
                buff = _normalize_jvread(raw)
                file_records.append({"rec_id": buff[:2], "data": buff})

            # 定期進捗ログ（5000件ごと or 60秒ごと）
            now = time.time()
            if read_count % 5000 == 0 or now - last_progress_time >= 60:
                logger.info(
                    f"  読込中: {read_count:,} 件 (ファイル={current_file})"
                )
                last_progress_time = now

    if not session_closed:
        jv.JVClose()

    logger.info(
        f"JVRead 完了: dataspec={dataspec}, 合計={read_count:,} レコード, "
        f"status={'完了' if all_completed else '時間制限中断'}"
    )
    return all_completed


# ---------------------------------------------------------------------------
# RACE データ取得
# ---------------------------------------------------------------------------

def run_historical_race(
    jv,
    from_date: str,
    stop_event: threading.Event,
) -> dict:
    """
    RACE DataSpec で 2000年以降のデータを取得する。

    option=1（差分モード）+ from_time="20000101000000" で全過去ファイルを対象とし、
    completed 記録済みのファイルは JVSkip で高速スキップする。
    再起動時は未処理ファイルから自動再開。

    option=4（セットアップ）は JVOpen が数時間フリーズするため使用しない。
    option=1 は JVOpen が数分で完了し JVRead で 1 ファイルずつ処理できる。

    Args:
        jv: JV-Link COMオブジェクト
        from_date: 取得開始日 YYYYMMDD (デフォルト 20000101)
        stop_event: 時間制限タイマーから set() される Event

    Returns:
        処理統計 dict
    """
    from_time = from_date + "000000"
    logger.info(f"=== RACE データ取得: from={from_time}, option=1 ===")

    completed = load_completed_files(COMPLETED_KEY_RACE)
    logger.info(f"処理済みファイル: {len(completed):,} 件 (JVSkip 対象)")

    total = {"files": 0, "skipped": 0, "ra_se": 0, "hr": 0, "start": time.time()}

    def on_file_done(filename: str, records: list[dict]) -> None:
        # JVSkip 経由のスキップ通知（records が空で completed にある）
        if filename in completed:
            total["skipped"] += 1
            return

        filtered = [r for r in records if r.get("rec_id") in ("RA", "SE", "HR")]
        ra_se = [r for r in filtered if r.get("rec_id") in ("RA", "SE")]
        hr = [r for r in filtered if r.get("rec_id") == "HR"]

        if not ra_se and not hr:
            # RA/SE/HR がないファイルも completed に記録（再処理防止）
            mark_file_completed(COMPLETED_KEY_RACE, filename, completed)
            return

        if ra_se:
            _post_in_batches(
                "/api/import/races", ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR
            )
            total["ra_se"] += len(ra_se)

        if hr:
            post_hr_payouts(hr)
            total["hr"] += len(hr)

        mark_file_completed(COMPLETED_KEY_RACE, filename, completed)
        total["files"] += 1

        if total["files"] % 200 == 0:
            elapsed = int(time.time() - total["start"])
            rate = total["files"] / elapsed * 3600 if elapsed > 0 else 0
            logger.info(
                f"進捗: {total['files']:,} ファイル完了 / {total['ra_se']:,} RA/SE件"
                f" (スキップ={total['skipped']:,}, {rate:.0f} ファイル/時)"
            )

    completed_fully = _fetch_with_stop(
        jv=jv,
        dataspec="RACE",
        from_time=from_time,
        option=1,
        on_file_done=on_file_done,
        skip_file_fn=lambda fn: fn in completed,
        stop_event=stop_event,
    )

    elapsed = int(time.time() - total["start"])
    logger.info(
        f"RACE 取得{'完了' if completed_fully else '（時間制限で中断）'}: "
        f"{total['files']:,} ファイル処理 / {total['skipped']:,} スキップ / "
        f"{total['ra_se']:,} RA/SE件 / {total['hr']:,} HR件 / 経過={elapsed}秒"
    )
    return total


# ---------------------------------------------------------------------------
# 馬マスタ（UM レコード）取得
# ---------------------------------------------------------------------------

def run_historical_horses(
    jv,
    stop_event: threading.Event,
) -> dict:
    """
    DIFN DataSpec で UM（競走馬マスタ）レコードを全期間取得する。

    jvlink_agent の blod-um モードと同じ completed キー（BLOD_UM）を使用。
    blod-um が完了済みの場合は大半がスキップされ高速に終わる。

    Args:
        jv: JV-Link COMオブジェクト
        stop_event: 時間制限タイマーから set() される Event

    Returns:
        処理統計 dict
    """
    from_time = "20000101000000"
    logger.info(f"=== UM 競走馬マスタ取得: DIFN, from={from_time}, option=1 ===")

    completed = load_completed_files(COMPLETED_KEY_HORSES)
    logger.info(f"処理済みファイル: {len(completed):,} 件 (JVSkip 対象)")

    total = {"files": 0, "skipped": 0, "um": 0, "start": time.time()}

    def on_file_done(filename: str, records: list[dict]) -> None:
        if filename in completed:
            total["skipped"] += 1
            return
        um_records = [r for r in records if r.get("rec_id") == "UM"]
        if um_records:
            _post_in_batches(
                "/api/import/bloodlines", um_records, 200, BACKEND_URL, API_KEY, PENDING_DIR
            )
            total["um"] += len(um_records)
        mark_file_completed(COMPLETED_KEY_HORSES, filename, completed)
        total["files"] += 1
        if total["files"] % 500 == 0:
            elapsed = int(time.time() - total["start"])
            rate = total["files"] / elapsed * 3600 if elapsed > 0 else 0
            logger.info(
                f"進捗: {total['files']:,} ファイル完了 / {total['um']:,} UM件"
                f" (スキップ={total['skipped']:,}, {rate:.0f} ファイル/時)"
            )

    completed_fully = _fetch_with_stop(
        jv=jv,
        dataspec="DIFN",
        from_time=from_time,
        option=1,
        on_file_done=on_file_done,
        skip_file_fn=lambda fn: fn in completed,
        stop_event=stop_event,
        max_errors=1000,  # DIFN は -402 エラーが多いため緩和
    )

    elapsed = int(time.time() - total["start"])
    logger.info(
        f"HORSES 取得{'完了' if completed_fully else '（時間制限で中断）'}: "
        f"{total['files']:,} ファイル処理 / {total['skipped']:,} スキップ / "
        f"{total['um']:,} UM件 / 経過={elapsed}秒"
    )
    return total


# ---------------------------------------------------------------------------
# 進捗表示
# ---------------------------------------------------------------------------

def show_status() -> None:
    """処理状況をログに出力する。"""
    race_completed = load_completed_files(COMPLETED_KEY_RACE)
    horses_completed = load_completed_files(COMPLETED_KEY_HORSES)

    logger.info("=== Historical Fetch Status ===")
    logger.info(f"  RACE   completed : {len(race_completed):,} ファイル")
    logger.info(f"  HORSES completed : {len(horses_completed):,} ファイル")

    if PENDING_DIR.exists():
        pending_files = list(PENDING_DIR.rglob("*.jsonl"))
        total_pending = 0
        for pf in pending_files:
            try:
                total_pending += sum(
                    1 for line in pf.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            except Exception:
                pass
        logger.info(f"  Pending          : {len(pending_files)} ファイル / {total_pending:,} レコード")

    if LOCK_FILE.exists():
        try:
            content = LOCK_FILE.read_text(encoding="utf-8").strip()
            logger.info(f"  Lock             : 実行中 ({content})")
        except Exception:
            logger.info("  Lock             : 実行中")
    else:
        logger.info("  Lock             : 停止中")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="kiseki Historical Data Fetcher — 2000年以降の全データを完全取得"
    )
    parser.add_argument(
        "--mode",
        choices=["all", "race", "horses"],
        default="all",
        help=(
            "取得モード: "
            "all=RACE+UM（デフォルト）, "
            "race=RACEのみ, "
            "horses=UM競走馬マスタのみ"
        ),
    )
    parser.add_argument(
        "--from-date",
        default="20000101",
        metavar="YYYYMMDD",
        help="RACE 取得開始日 (デフォルト: 20000101。option=1 差分モードで該当日以降を取得)",
    )
    parser.add_argument(
        "--time-limit",
        type=int,
        default=7200,
        metavar="SECONDS",
        help="実行時間制限（秒）。超過後はファイル完了単位で graceful stop (デフォルト: 7200=2時間)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="進捗状況を表示して終了",
    )
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    # 多重起動チェック
    if check_already_running():
        logger.info("既に historical_agent が実行中です。スキップします。")
        sys.exit(0)

    write_lock()

    try:
        _run_main(args)
    finally:
        release_lock()


def _run_main(args) -> None:
    if not JRAVAN_SID:
        logger.error("JRAVAN_SID が未設定。C:\\kiseki\\.env を確認してください。")
        sys.exit(1)

    logger.info(
        f"=== Historical Data Fetcher 開始 ==="
        f" mode={args.mode}, from={args.from_date}, time_limit={args.time_limit}s"
    )

    # メンテナンス窓中は COM を初期化する前に降りる。JVInit 自体はサーバーへ
    # アクセスしないが、ここで抜ければダイアログもロック保持も発生しない。
    if reason := jvm.skip_reason("Historical Data Fetcher", logger=logger):
        logger.warning(reason)
        logger.info("=== メンテナンス窓のため今回は見送り。次回スケジュール実行で再開します ===")
        return

    # ペンディングリトライ（前回 POST 失敗分を再送）
    retry_pending(PENDING_DIR, BACKEND_URL, API_KEY)

    # 時間制限タイマー
    stop_event = threading.Event()
    start_time = time.time()

    def _timer():
        time.sleep(args.time_limit)
        logger.info(
            f"[timer] 時間制限 {args.time_limit}秒 到達。"
            f"次のファイル完了後に停止します。"
        )
        stop_event.set()

    timer_thread = threading.Thread(target=_timer, daemon=True)
    timer_thread.start()

    # JV-Link 初期化（デスクトップセッション必須 → RunAdhoc 経由で起動すること）
    jv = init_jvlink()

    try:
        if args.mode in ("all", "race"):
            run_historical_race(jv, args.from_date, stop_event)

        if args.mode in ("all", "horses") and not stop_event.is_set():
            run_historical_horses(jv, stop_event)

    except jvm.MaintenanceWindowActive as e:
        # 異常ではない。処理済みファイルは completed に記録済みなので次回続行できる。
        logger.warning(f"メンテナンスのため中断: {e}")
    except KeyboardInterrupt:
        logger.info("Ctrl+C で中断。次回起動時に続きから再開します。")
    except Exception as e:
        logger.error(f"予期せぬエラー: {e}", exc_info=True)
    finally:
        try:
            jv.JVClose()
        except Exception:
            pass

    elapsed = int(time.time() - start_time)
    if stop_event.is_set():
        logger.info(
            f"=== 時間制限で中断 (経過={elapsed}秒) ==="
            f" 次回実行時に続きから再開します。"
        )
    else:
        logger.info(f"=== 全データ取得完了 (経過={elapsed}秒) ===")


if __name__ == "__main__":
    main()
