"""
kiseki Windows Agent - UmaConn SDK データ取得・リアルタイム通知

地方競馬データを UmaConn SDK (NVDTLabLib.NVLink) から取得し、
Mac側 FastAPI の chihou エンドポイントへ送信する。

UmaConn 固有の仕様:
  - DataSpec は RACE / BLOD / DIFF / SLOP のみ利用可能
  - 取得可能期間は 2005-01-01 以降のみ
  - NVSkip は信頼できないため、-1 返時もスキップせず継続読み込み
  - to_time を指定しても最新まで全データが返る（フィルタ不要）
  - タイムアウトは経過日数から動的に計算

ローカルキャッシュ機能:
  - NVRead 後すぐにローカル JSONL ファイルへ保存
  - 同一キーのデータがキャッシュ済みなら NVOpen をスキップ
  - POST 失敗分はペンディングキューへ保存し、次回起動時に自動リトライ

動作環境:
  - Windows 10/11 (Parallels 上でも可)
  - Python 3.x 32bit 版 (UmaConn が 32bit COM のため必須)
  - pywin32
  - UmaConn インストール済み + 利用設定済み

使い方:
  python umaconn_agent.py --mode setup      # 2005-01-01 から全データ一括取得
  python umaconn_agent.py --mode daily      # 昨日から当日データ取得
  python umaconn_agent.py --mode recent --from-year 2023  # 指定年以降を取得
  python umaconn_agent.py --mode realtime   # 30 秒ごとにオッズ・成績をポーリング
  python umaconn_agent.py --mode retry      # ペンディングキューのリトライのみ
"""

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from link_common import (
    _normalize_jvread,
    post_to_backend,
    _post_in_batches,
    save_cache,
    load_cache,
    save_pending,
    retry_pending,
    report_status as _lc_report_status,
)

# ---------------------------------------------------------------------------
# 環境変数
# ---------------------------------------------------------------------------

# .env はプロジェクトルートにある想定
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

BACKEND_URL: str = os.getenv("UMACONN_BACKEND_URL", "http://YuichironoMacBook-Pro-6.local:8000")
API_KEY: str = os.getenv("UMACONN_API_KEY", "")

# ---------------------------------------------------------------------------
# ローカルデータディレクトリ
# ---------------------------------------------------------------------------

_AGENT_DIR = Path(__file__).resolve().parent
DATA_DIR = _AGENT_DIR / "data"
CACHE_DIR: Path = DATA_DIR / "chihou_cache"
COMPLETED_DIR: Path = DATA_DIR / "chihou_completed"
PENDING_DIR: Path = DATA_DIR / "chihou_pending"

# ---------------------------------------------------------------------------
# ロギング設定
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("umaconn_agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("umaconn_agent")

# ---------------------------------------------------------------------------
# UmaConn DataSpec 定数
# ---------------------------------------------------------------------------

# 蓄積系 (NVOpen) — UmaConn で利用可能なもののみ
DATASPEC_RACE = "RACE"   # レース情報(RA) + 馬毎レース情報(SE)
DATASPEC_BLOD = "BLOD"   # 血統
DATASPEC_DIFF = "DIFF"   # 差分（レース・出走情報）
DATASPEC_SLOP = "SLOP"   # 坂路調教

# 速報系 (NVRTOpen)
RT_ODDS = "0B31"   # 速報オッズ（単複枠）key = レースキー 16 文字
RT_RESULT = "0B12"  # 速報成績（払戻確定後）

# UmaConn 取得可能開始日時の下限
UMACONN_EARLIEST: str = "20050101000000"

# chihou エンドポイント
EP_RACES = "/api/import/chihou/races"
EP_ODDS = "/api/import/chihou/odds"
EP_BLOODLINES = "/api/import/chihou/bloodlines"
EP_PAYOUTS = "/api/import/chihou/payouts"


# ---------------------------------------------------------------------------
# タイムアウト計算
# ---------------------------------------------------------------------------

def _calc_timeout_seconds() -> int:
    """UmaConn の NVOpen タイムアウトを 2005-01-01 からの経過日数で動的に計算する。

    地方競馬の全データ（2005 年〜）は JRA-VAN より件数が多いため、
    固定タイムアウトでは不足する場合がある。

    Returns:
        タイムアウト秒数（最低 120 秒）
    """
    days_elapsed = (datetime.now() - datetime(2005, 1, 1)).days
    timeout_seconds = int((days_elapsed / 365) * 60 + 60)
    return max(timeout_seconds, 120)


# ---------------------------------------------------------------------------
# ファイル単位の処理完了ログ
# ---------------------------------------------------------------------------

def _completed_path(dataspec: str) -> Path:
    """処理完了ログファイルのパスを返す。

    Args:
        dataspec: UmaConn データ種別ID（例: "RACE"）

    Returns:
        完了ログファイルの Path
    """
    return COMPLETED_DIR / f"{dataspec}_completed.txt"


def load_completed_files(dataspec: str) -> set[str]:
    """処理済みファイル名のセットを返す。

    Args:
        dataspec: UmaConn データ種別ID

    Returns:
        処理済みファイル名の set
    """
    path = _completed_path(dataspec)
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def mark_file_completed(dataspec: str, filename: str) -> None:
    """ファイルを処理済みとして記録する。

    Args:
        dataspec: UmaConn データ種別ID
        filename: 完了としてマークするファイル名
    """
    COMPLETED_DIR.mkdir(parents=True, exist_ok=True)
    with _completed_path(dataspec).open("a", encoding="utf-8") as f:
        f.write(filename + "\n")


# ---------------------------------------------------------------------------
# UmaConn 初期化
# ---------------------------------------------------------------------------

def init_umaconn():
    """UmaConn COM オブジェクトを初期化して返す。

    NVInit の引数は UmaConn の仕様により "UNKNOWN" 固定。

    Returns:
        初期化済みの NVLink COM オブジェクト

    Raises:
        SystemExit: 初期化に失敗した場合
    """
    try:
        import win32com.client  # noqa: PLC0415 (Windows 環境専用)
        nv = win32com.client.Dispatch("NVDTLabLib.NVLink")
        rc = nv.NVInit("UNKNOWN")
        if rc != 0:
            logger.error(f"NVInit failed: rc={rc}")
            sys.exit(1)
        # サービスキーを設定（NVOpen前に必須）
        # タイムアウト付き実行: NVSetServiceKey はサービスサーバーへのネットワーク接続を行い
        # サーバーが応答しない場合に数分以上ブロックすることがある。
        # スレッドで実行して最大60秒待機し、超過した場合は警告して続行する。
        if API_KEY:
            _key_result: list = []

            def _set_key() -> None:
                try:
                    rc_key = nv.NVSetServiceKey(API_KEY)
                    _key_result.append(rc_key)
                except Exception as e:  # noqa: BLE001
                    _key_result.append(f"error: {e}")

            _t = threading.Thread(target=_set_key, daemon=True)
            _t.start()
            _t.join(timeout=60)
            if _t.is_alive():
                logger.warning(
                    "NVSetServiceKey: 60秒タイムアウト。サービスキー未設定のまま続行します。"
                )
            else:
                rc_key = _key_result[0] if _key_result else "no_result"
                logger.info(f"NVSetServiceKey rc={rc_key}")
        else:
            logger.warning("UMACONN_API_KEY が未設定です")
        logger.info("UmaConn initialized successfully")
        return nv
    except Exception as e:
        logger.error(f"UmaConn initialization error: {e}")
        logger.error("Python 32bit 版で実行していますか？ UmaConn はインストール済みですか？")
        sys.exit(1)


# ---------------------------------------------------------------------------
# UmaConn 蓄積系データ取得
# ---------------------------------------------------------------------------

def fetch_stored_data(
    nv,
    dataspec: str,
    from_time: str,
    option: int = 1,
    on_file_done=None,
    skip_cache: bool = False,
) -> list[dict]:
    """蓄積系データを取得する (NVOpen)。

    UmaConn 固有の仕様に対処しながら NVRead ループを実行する:
      - NVSkip は信頼できないため、ret_code == -1 でもスキップせず継続
      - to_time を指定しても最新まで全データが返る（フィルタ不要）
      - タイムアウトは _calc_timeout_seconds() で動的に計算

    キャッシュが存在する場合は NVOpen をスキップしてキャッシュから返す。
    取得成功後はローカルキャッシュへ保存する（skip_cache=True の場合を除く）。

    Args:
        nv: UmaConn COM オブジェクト
        dataspec: データ種別ID（例: "RACE"）
        from_time: 取得開始日時 "YYYYMMDDhhmmss"（2005-01-01 以降）
        option: 1=通常, 2=今週, 3=セットアップ
        on_file_done: ファイル 1 本読み込み完了時のコールバック
                      signature: on_file_done(filename: str, records: list[dict])
        skip_cache: True の場合キャッシュを使用せず、on_file_done で逐次処理する

    Returns:
        取得したレコードのリスト（skip_cache=True の場合は空リスト）
    """
    # 開始日時の下限チェック（UmaConn は 2005-01-01 以降のみ）
    if from_time < UMACONN_EARLIEST:
        logger.warning(
            f"from_time={from_time} は UmaConn の下限 {UMACONN_EARLIEST} より前です。"
            f" → {UMACONN_EARLIEST} に補正します。"
        )
        from_time = UMACONN_EARLIEST

    # キャッシュ確認
    if not skip_cache:
        cached = load_cache(dataspec, from_time, option, CACHE_DIR)
        if cached is not None:
            logger.info(
                f"[cache] キャッシュ使用: {dataspec} from={from_time} opt={option} ({len(cached)} records)"
            )
            return cached

    # NVOpen で取得開始
    logger.info(f"NVOpen 呼び出し開始: dataspec={dataspec}, from={from_time}, option={option}")
    _nvopen_done = threading.Event()

    def _heartbeat() -> None:
        """NVOpen のブロッキング中にハートビートログを出力する。"""
        start = time.time()
        while not _nvopen_done.is_set():
            _nvopen_done.wait(timeout=30)
            if not _nvopen_done.is_set():
                elapsed = int(time.time() - start)
                logger.info(f"NVOpen 待機中... 経過={elapsed}秒 (NVOpen がブロッキング中)")

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()

    result = nv.NVOpen(dataspec, from_time, option, 0, 0, "")
    _nvopen_done.set()

    if isinstance(result, tuple):
        rc = result[0]
        read_count_total = result[1] if len(result) > 1 else "?"
        dl_count = result[2] if len(result) > 2 else "?"
        last_ts = result[3] if len(result) > 3 else "?"
        logger.info(
            f"NVOpen 戻り値: rc={rc}, 読込ファイル数={read_count_total}, "
            f"DL数={dl_count}, 最終TS={last_ts}"
        )
    else:
        rc = result
        logger.info(f"NVOpen 戻り値: rc={rc}")

    if rc < 0:
        if rc == -202:
            # rc=-202 は「前回取得以降に新データなし」を意味する（エラーではない）。
            # NVOpen が内部セッション状態を「空オープン」したままにする場合があるため、
            # 必ず NVClose を呼んでオブジェクトを中立状態に戻す。
            # これを怠ると同一 nv オブジェクトでの NVRTOpen がブロックされる。
            logger.debug(f"NVOpen: 新データなし (rc=-202, dataspec={dataspec})")
            nv.NVClose()
        else:
            logger.error(f"NVOpen エラー: rc={rc}")
            nv.NVClose()
        return []

    logger.info(f"NVRead ループ開始 (rc={rc} ファイル)")

    all_records: list[dict] = []   # 全ファイル分の累積（キャッシュ保存用）
    file_records: list[dict] = []  # 現在ファイル分（コールバック用）
    read_count = 0
    current_file = ""
    last_log_time = time.time()
    wait_count = 0
    error_count = 0
    MAX_ERRORS = 5
    session_closed = False

    def _flush_file(fname: str) -> None:
        """現在ファイルのレコードをコールバックへ渡し、累積リストへ移す。"""
        nonlocal file_records
        if fname and on_file_done:
            on_file_done(fname, file_records)
        if not skip_cache:
            all_records.extend(file_records)
        file_records = []

    while True:
        r = nv.NVRead("", 256000, "")
        ret_code = r[0]

        if ret_code == 0:
            # EOF: 最終ファイルをフラッシュして完了
            logger.info("NVRead: EOF 到達 → 読み込み完了")
            _flush_file(current_file)
            break

        elif ret_code == -1:
            # UmaConn 仕様: NVSkip は信頼できないため、-1 でもスキップせず継続
            # JV-Link と異なりファイル切り替わりの確実な通知ではないため、
            # ファイル名変化を見てフラッシュ判断をする
            new_file = r[3] if len(r) > 3 else (r[2] if len(r) > 2 else "")
            if new_file and new_file != current_file:
                if current_file:
                    logger.info(
                        f"NVRead: ファイル完了 {current_file} "
                        f"({len(file_records)} 件) → 次ファイル: {new_file}"
                    )
                    _flush_file(current_file)
                current_file = new_file
            # -1 は継続読み込み（スキップしない）
            continue

        elif ret_code == -3:
            # ダウンロード待機中
            wait_count += 1
            now = time.time()
            if now - last_log_time >= 30:
                logger.info(
                    f"NVRead: ダウンロード待機中... "
                    f"(待機回数={wait_count}, 取得済={read_count}件, "
                    f"ファイル={current_file or '未開始'})"
                )
                last_log_time = now
                wait_count = 0
            time.sleep(0.5)
            continue

        elif ret_code < -1:
            logger.error(f"NVRead エラー: rc={ret_code}, ファイル={current_file}")
            error_count += 1
            file_records = []  # 不完全データを破棄

            if on_file_done and current_file:
                logger.warning(f"エラーファイル {current_file} を completed としてマーク (エラースキップ)")
                on_file_done(current_file, [])

            if error_count >= MAX_ERRORS:
                logger.error(f"エラーが {MAX_ERRORS} 回以上発生。処理を中断します。")
                nv.NVClose()
                session_closed = True
                break

            # NVClose → NVOpen(option=1) でセッション再開を試みる
            nv.NVClose()
            session_closed = True
            logger.info(
                f"NVRead エラー後 NVClose。option=1 でセッション再開を試みます... "
                f"(エラー {error_count}/{MAX_ERRORS})"
            )
            result2 = nv.NVOpen(dataspec, from_time, 1, 0, 0, "")
            rc2 = result2[0] if isinstance(result2, tuple) else result2
            if rc2 < 0:
                logger.error(f"NVOpen 再開失敗: rc={rc2}。処理を中断します。")
                break
            logger.info(f"NVOpen 再開成功 (option=1): rc={rc2}。残りファイルの読み取りを続けます。")
            session_closed = False
            current_file = ""
            continue

        else:
            # 正常レコード
            read_count += 1
            wait_count = 0
            buff = _normalize_jvread(r[1])
            rec_id = buff[:2]
            file_records.append({"rec_id": rec_id, "data": buff})

            # ファイル名変化でフラッシュ
            new_file = r[3] if len(r) > 3 else ""
            if new_file and new_file != current_file:
                if current_file:
                    logger.info(
                        f"NVRead: ファイル完了 {current_file} "
                        f"({len(file_records) - 1} 件) → 次ファイル: {new_file}"
                    )
                    # 現在追加したレコードを除いてフラッシュ
                    last_rec = file_records.pop()
                    _flush_file(current_file)
                    file_records.append(last_rec)
                current_file = new_file

            if read_count % 1000 == 0:
                logger.info(f"  ... {read_count} 件読込済 (現在ファイル: {current_file})")

    if not session_closed:
        nv.NVClose()
    logger.info(f"NVOpen 完了: {read_count} 件取得 from {dataspec}")

    records = all_records

    if not skip_cache:
        save_cache(dataspec, from_time, option, records, CACHE_DIR)

    return records


# ---------------------------------------------------------------------------
# UmaConn 速報系データ取得
# ---------------------------------------------------------------------------

def fetch_realtime_data(nv, dataspec: str, key: str) -> list[dict]:
    """速報系データを取得する (NVRTOpen)。

    Args:
        nv: UmaConn COM オブジェクト
        dataspec: データ種別ID（例: "0B31"）
        key: レースキー（例: "2026032205010105"）

    Returns:
        取得したレコードのリスト
    """
    rc = nv.NVRTOpen(dataspec, key)
    if rc < 0:
        logger.debug(f"NVRTOpen no data: rc={rc}, dataspec={dataspec}, key={key[:16]}")
        return []

    records: list[dict] = []
    # 時間ベースのタイムアウト: データが一切来ない場合は IDLE_TIMEOUT 秒で打ち切る。
    # データを受信している間は last_data_time をリセットし続けるため打ち切らない。
    IDLE_TIMEOUT = 30.0  # データ無受信でのタイムアウト（秒）
    last_data_time = time.time()

    while True:
        r = nv.NVRead("", 256000, "")
        ret_code = r[0]

        if ret_code == 0:
            break
        elif ret_code in (-1, -3):
            # -1: ファイル切り替わり待ち  -3: ダウンロード中
            # データが一定時間来なければ打ち切る
            if time.time() - last_data_time > IDLE_TIMEOUT:
                logger.warning(
                    f"NVRead idle timeout {IDLE_TIMEOUT}s: 強制終了 ({dataspec} {key[:16]})"
                )
                break
            time.sleep(0.2)
            continue
        elif ret_code < -3:
            logger.error(f"NVRead (realtime) error: rc={ret_code}")
            break
        else:
            last_data_time = time.time()  # データ受信でタイマーリセット
            buff = _normalize_jvread(r[1])
            rec_id = buff[:2]
            records.append({"rec_id": rec_id, "data": buff})

    nv.NVClose()
    return records


# ---------------------------------------------------------------------------
# フィルタリングユーティリティ
# ---------------------------------------------------------------------------

def _filter_race_records(records: list[dict]) -> list[dict]:
    """RA/SE/HR レコードのみ抽出する。

    RACE dataspec には対象外のレコードも混在するため。

    Args:
        records: 全レコードのリスト

    Returns:
        RA/SE/HR のみのリスト
    """
    return [r for r in records if r.get("rec_id") in ("RA", "SE", "HR")]


def _split_race_hr(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """RA/SE と HR レコードに分割する。

    Args:
        records: RA/SE/HR の混在リスト

    Returns:
        (ra_se_records, hr_records)
    """
    ra_se = [r for r in records if r.get("rec_id") in ("RA", "SE")]
    hr = [r for r in records if r.get("rec_id") == "HR"]
    return ra_se, hr


def _post_hr_payouts(hr_records: list[dict]) -> None:
    """HR レコードを chihou 払戻エンドポイントへ送信する。

    Args:
        hr_records: rec_id="HR" のレコードリスト
    """
    if not hr_records:
        return
    ok = post_to_backend(EP_PAYOUTS, {"records": hr_records}, BACKEND_URL, API_KEY)
    if ok:
        logger.info(f"  POST {EP_PAYOUTS} {len(hr_records)} 件 -> OK")
    else:
        logger.warning(f"  POST {EP_PAYOUTS} {len(hr_records)} 件 -> NG (ペンディング保存)")
        save_pending(EP_PAYOUTS, hr_records, PENDING_DIR)


# ---------------------------------------------------------------------------
# ステータス報告
# ---------------------------------------------------------------------------

def report_status(
    status: str,
    mode: str | None = None,
    message: str = "",
    progress: dict | None = None,
) -> None:
    """バックエンドへ現在のステータスを送信するローカルラッパー。

    link_common.report_status を BACKEND_URL / API_KEY で呼び出す。

    Args:
        status: "running" | "idle" | "error" | "done"
        mode: 動作モード名（例: "setup", "daily"）
        message: 状態の説明
        progress: 任意の進捗情報（None の場合は空 dict）
    """
    _lc_report_status(status, mode, message, progress, BACKEND_URL, API_KEY)


# ---------------------------------------------------------------------------
# バックエンドからレースキーを取得
# ---------------------------------------------------------------------------

def _fetch_today_race_keys(date: str) -> list[str]:
    """バックエンド API から指定日の地方競馬レースキー一覧を取得する。

    Args:
        date: 対象日（YYYYMMDD）

    Returns:
        UmaConn レースキー（umaconn_race_id、16 文字文字列）のリスト
    """
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/chihou/races/race-keys",
            params={"date": date},
            timeout=5,
        )
        if resp.status_code == 200:
            races = resp.json()
            keys = [r["race_key"] for r in races if r.get("race_key")]
            logger.debug(f"レースキー取得: {len(keys)} 件 ({date})")
            return keys
    except Exception as e:
        logger.warning(f"レースキー取得失敗: {e}")
    return []


# ---------------------------------------------------------------------------
# 動作モード: setup
# ---------------------------------------------------------------------------

def run_setup(nv) -> None:
    """初回セットアップ（2005-01-01 からの全データ一括取得）。

    option=3（セットアップモード）で UmaConn の全データを取得する。
    ファイル単位の完了ログにより:
      - 処理済みファイルは再起動時も二重登録をスキップ
      - 中断後の再起動で未処理ファイルから再開可能
    """
    logger.info("=== SETUP MODE: 2005-01-01 から全データ一括取得 ===")
    logger.info("※ option=3 で UmaConn 全過去ファイルを取得します。")
    logger.info("※ ファイル 1 本完了ごとに即時 DB へ反映します。")
    logger.info("※ 処理済みファイルは再起動時にスキップします。")

    from_time = UMACONN_EARLIEST  # 2005-01-01 以降のみ

    # ----- RACE -----
    completed = load_completed_files(DATASPEC_RACE)
    if completed:
        logger.info(f"[completed] 処理済みファイル (RACE): {len(completed)} 件")

    total_race: dict[str, int] = {"ra_se": 0, "files": 0, "skipped": 0}

    def on_race_file_done(filename: str, file_records: list[dict]) -> None:
        """レース 1 ファイル完了コールバック。"""
        if filename in completed:
            total_race["skipped"] += 1
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
                f"  [{filename}] RA/SE {len(ra_se)} 件 / 全 {len(file_records)} 件 → DB 反映開始"
            )
            _post_in_batches(EP_RACES, ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
            total_race["ra_se"] += len(ra_se)
        if hr:
            logger.info(f"  [{filename}] HR {len(hr)} 件 → 払戻 DB 反映")
            _post_hr_payouts(hr)
        total_race["files"] += 1
        mark_file_completed(DATASPEC_RACE, filename)
        completed.add(filename)
        logger.info(
            f"  [{filename}] 完了 (累計: {total_race['files']} ファイル / {total_race['ra_se']} 件)"
        )

    logger.info(f"Fetching RACE from {from_time} (option=3)...")
    fetch_stored_data(
        nv, DATASPEC_RACE, from_time, option=3,
        on_file_done=on_race_file_done,
    )
    logger.info(
        f"RACE 取得完了: {total_race['files']} ファイル / "
        f"{total_race['ra_se']} 件 DB 反映 / {total_race['skipped']} スキップ"
    )

    # ----- BLOD -----
    completed_blod = load_completed_files(DATASPEC_BLOD)
    total_blod: dict[str, int] = {"hn_sk": 0, "files": 0, "skipped": 0}

    def on_blod_file_done(filename: str, file_records: list[dict]) -> None:
        """血統 1 ファイル完了コールバック。"""
        if filename in completed_blod:
            total_blod["skipped"] += 1
            return
        hn_sk = [r for r in file_records if r.get("rec_id") in ("HN", "SK")]
        if not hn_sk:
            mark_file_completed(DATASPEC_BLOD, filename)
            completed_blod.add(filename)
            return
        logger.info(f"  [{filename}] HN/SK {len(hn_sk)} 件 → DB 反映開始")
        _post_in_batches(EP_BLOODLINES, hn_sk, 500, BACKEND_URL, API_KEY, PENDING_DIR)
        total_blod["hn_sk"] += len(hn_sk)
        total_blod["files"] += 1
        mark_file_completed(DATASPEC_BLOD, filename)
        completed_blod.add(filename)
        logger.info(
            f"  [{filename}] 完了 (累計: {total_blod['files']} ファイル / {total_blod['hn_sk']} 件)"
        )

    logger.info(f"Fetching BLOD from {from_time} (option=3)...")
    fetch_stored_data(
        nv, DATASPEC_BLOD, from_time, option=3,
        on_file_done=on_blod_file_done,
    )
    logger.info(
        f"BLOD 取得完了: {total_blod['files']} ファイル / "
        f"{total_blod['hn_sk']} 件 DB 反映 / {total_blod['skipped']} スキップ"
    )

    # ----- DIFF -----
    completed_diff = load_completed_files(DATASPEC_DIFF)
    total_diff: dict[str, int] = {"records": 0, "files": 0, "skipped": 0}

    def on_diff_file_done(filename: str, file_records: list[dict]) -> None:
        """DIFF 1 ファイル完了コールバック（RA/SE のみ送信）。"""
        if filename in completed_diff:
            total_diff["skipped"] += 1
            return
        filtered = _filter_race_records(file_records)
        ra_se, hr = _split_race_hr(filtered)
        if not ra_se and not hr:
            mark_file_completed(DATASPEC_DIFF, filename)
            completed_diff.add(filename)
            return
        if ra_se:
            _post_in_batches(EP_RACES, ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
            total_diff["records"] += len(ra_se)
        if hr:
            _post_hr_payouts(hr)
        total_diff["files"] += 1
        mark_file_completed(DATASPEC_DIFF, filename)
        completed_diff.add(filename)

    logger.info(f"Fetching DIFF from {from_time} (option=3)...")
    fetch_stored_data(
        nv, DATASPEC_DIFF, from_time, option=3,
        on_file_done=on_diff_file_done,
    )
    logger.info(
        f"DIFF 取得完了: {total_diff['files']} ファイル / "
        f"{total_diff['records']} 件 DB 反映 / {total_diff['skipped']} スキップ"
    )


# ---------------------------------------------------------------------------
# 動作モード: daily
# ---------------------------------------------------------------------------

def run_daily_fetch(nv) -> None:
    """当日データ取得（毎朝実行）。

    昨日 00:00 以降の差分データを option=2 で取得し、DB へ反映する。
    """
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d") + "000000"
    logger.info(f"=== DAILY MODE: {yesterday} 以降のデータ取得 ===")

    def on_daily_file_done(filename: str, file_records: list[dict]) -> None:
        """デイリー取得 1 ファイル完了コールバック。"""
        filtered = _filter_race_records(file_records)
        ra_se, hr = _split_race_hr(filtered)
        if ra_se:
            logger.info(f"  [{filename}] RA/SE {len(ra_se)} 件 → DB 反映")
            _post_in_batches(EP_RACES, ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
        if hr:
            logger.info(f"  [{filename}] HR {len(hr)} 件 → 払戻 DB 反映")
            _post_hr_payouts(hr)

    records = fetch_stored_data(
        nv, DATASPEC_RACE, yesterday, option=2,
        on_file_done=on_daily_file_done,
    )
    logger.info(f"Daily fetch 完了: 全体 {len(records)} 件")


# ---------------------------------------------------------------------------
# 動作モード: recent
# ---------------------------------------------------------------------------

def run_recent(nv, from_year: int = 2023) -> None:
    """指定年以降のデータを優先取得する。

    option=3 + from_time で UmaConn 側がその年以降のファイルのみを返す。
    skip_cache=True でメモリ蓄積なし（大量データ対策）。

    Args:
        nv: UmaConn COM オブジェクト
        from_year: この年以降のデータを取得する（例: 2023）
    """
    from_time = f"{from_year}0101000000"
    # UmaConn 下限チェック
    if from_time < UMACONN_EARLIEST:
        logger.warning(
            f"from_year={from_year} は UmaConn 下限 (2005) より前です。2005 に補正します。"
        )
        from_time = UMACONN_EARLIEST

    logger.info(f"=== RECENT MODE: {from_year} 年以降データを取得 (option=3, from={from_time}) ===")

    completed = load_completed_files(DATASPEC_RACE)
    if completed:
        logger.info(f"[completed] 処理済みファイル: {len(completed)} 件（スキップ対象）")

    total_posted: dict[str, int] = {"ra_se": 0, "files": 0, "skipped": 0}

    def on_race_file_done(filename: str, file_records: list[dict]) -> None:
        """RECENT レース 1 ファイル完了コールバック。"""
        if not file_records and filename not in completed:
            mark_file_completed(DATASPEC_RACE, filename)
            completed.add(filename)
            total_posted["skipped"] += 1
            return
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
                f"  [{filename}] RA/SE {len(ra_se)} 件 / 全 {len(file_records)} 件 → DB 反映開始"
            )
            _post_in_batches(EP_RACES, ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
            total_posted["ra_se"] += len(ra_se)
        if hr:
            logger.info(f"  [{filename}] HR {len(hr)} 件 → 払戻 DB 反映")
            _post_hr_payouts(hr)
        total_posted["files"] += 1
        mark_file_completed(DATASPEC_RACE, filename)
        completed.add(filename)
        logger.info(
            f"  [{filename}] 完了 (累計: {total_posted['files']} ファイル / {total_posted['ra_se']} 件)"
        )

    logger.info(f"Fetching RACE (option=3, from={from_time})...")
    fetch_stored_data(
        nv, DATASPEC_RACE, from_time, option=3,
        on_file_done=on_race_file_done,
        skip_cache=True,
    )
    logger.info(
        f"RECENT 完了: {total_posted['files']} ファイル / "
        f"{total_posted['ra_se']} 件 DB 反映 / {total_posted['skipped']} スキップ"
    )


# ---------------------------------------------------------------------------
# 動作モード: realtime
# ---------------------------------------------------------------------------

def run_realtime_monitor(nv) -> None:
    """リアルタイム監視ループ（約30秒間隔）。

    - 0B31 (オッズ): メインスレッドで取得 → chihou/odds へ POST
    - 0B12 (速報成績): 別スレッド・別COMオブジェクトでバックグラウンド実行。
      NVRTOpen("0B12") はデータ準備中にブロックすることがある。
      そのため sleep 中に並行して実行し、完了した結果のみ次サイクルで処理する。
    """
    logger.info("=== REALTIME MODE: リアルタイム監視開始 ===")
    today = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d") + "000000"
    seen_results: set[str] = set()
    cycle = 0
    INCREMENTAL_EVERY = 10  # 約5分ごと（30秒×10）に蓄積系差分取得

    # 0B12 バックグラウンドフェッチ管理
    _bg_result_buf: list[dict] = []
    _bg_thread: threading.Thread | None = None

    def _start_bg_results_fetch(keys: list[str]) -> threading.Thread:
        """0B12 を別スレッド・別COMオブジェクトで取得し _bg_result_buf に蓄積する。"""
        _bg_result_buf.clear()

        def worker() -> None:
            try:
                import win32com.client  # noqa: PLC0415
                nv2 = win32com.client.Dispatch("NVDTLabLib.NVLink")
                rc = nv2.NVInit("UNKNOWN")
                if rc != 0:
                    return
                for race_key in keys:
                    for rec in fetch_realtime_data(nv2, RT_RESULT, race_key):
                        if rec.get("rec_id") in ("RA", "SE", "HR"):
                            _bg_result_buf.append(rec)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"bg_results_fetch error: {e}")

        t = threading.Thread(target=worker, daemon=True, name="bg-0b12")
        t.start()
        return t

    while True:
        try:
            # 日付跨ぎ検知: 毎サイクル今日の日付を更新
            current_date = datetime.now().strftime("%Y%m%d")
            if current_date != today:
                logger.info(f"日付変更検知: {today} → {current_date}. seen_results をリセット")
                today = current_date
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d") + "000000"
                seen_results = set()

            # 本日のレースキーを取得
            race_keys = _fetch_today_race_keys(today)
            if not race_keys:
                logger.debug("本日の地方競馬レースキーが取得できませんでした")

            # ----- 前サイクルの 0B12 結果を処理 -----
            if _bg_thread is not None and not _bg_thread.is_alive():
                new_results = []
                for rec in _bg_result_buf:
                    key = rec["data"][:30]
                    if key not in seen_results:
                        seen_results.add(key)
                        new_results.append(rec)
                if new_results:
                    ra_se, hr = _split_race_hr(new_results)
                    if ra_se:
                        logger.info(f"速報成績取得(0B12): {len(ra_se)} 件 (RA/SE) → chihou/races へ送信")
                        post_to_backend(EP_RACES, {"records": ra_se}, BACKEND_URL, API_KEY)
                    if hr:
                        logger.info(f"払戻取得(0B12): {len(hr)} 件 (HR) → chihou/payouts へ送信")
                        _post_hr_payouts(hr)
                _bg_thread = None

            # ----- 蓄積系差分取得 (option=2) — 約5分ごとに確定成績をポーリング -----
            if cycle % INCREMENTAL_EVERY == 0:
                incremental_total = [0]

                def on_incremental_file(filename: str, file_records: list[dict]) -> None:
                    filtered = _filter_race_records(file_records)
                    ra_se, hr = _split_race_hr(filtered)
                    if ra_se:
                        logger.info(
                            f"[蓄積差分] {filename}: RA/SE {len(ra_se)} 件 → DB 反映"
                        )
                        _post_in_batches(EP_RACES, ra_se, 500, BACKEND_URL, API_KEY, PENDING_DIR)
                        incremental_total[0] += len(ra_se)
                    if hr:
                        logger.info(f"[蓄積差分] {filename}: HR {len(hr)} 件 → 払戻 DB 反映")
                        _post_hr_payouts(hr)

                fetch_stored_data(
                    nv, DATASPEC_RACE, yesterday, option=2,
                    on_file_done=on_incremental_file,
                    skip_cache=True,
                )
                if incremental_total[0] > 0:
                    logger.info(f"蓄積差分取得完了: 合計 {incremental_total[0]} 件 DB 反映")

            # ----- オッズ取得 (0B31) -----
            all_odds: list[dict] = []
            for race_key in race_keys:
                odds_records = fetch_realtime_data(nv, RT_ODDS, race_key)
                odds = [r for r in odds_records if r.get("rec_id", "").startswith("O")]
                all_odds.extend(odds)

            if all_odds:
                logger.info(f"オッズ取得: {len(all_odds)} 件 / {len(race_keys)} レース → chihou/odds へ送信")
                post_to_backend(EP_ODDS, {"date": today, "records": all_odds}, BACKEND_URL, API_KEY)

            # ----- 0B12 フェッチをバックグラウンドで開始（前サイクルが完了している場合のみ） -----
            if _bg_thread is None or not _bg_thread.is_alive():
                _bg_thread = _start_bg_results_fetch(race_keys)
            else:
                logger.debug("0B12 前サイクルのフェッチがまだ実行中 — 今回はスキップ")

            cycle += 1
            time.sleep(30)

        except KeyboardInterrupt:
            logger.info("Realtime monitor stopped by user")
            break
        except Exception as e:
            logger.error(f"Realtime monitor error: {e}")
            time.sleep(10)


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    """コマンドライン引数を解析して各動作モードを実行する。"""
    parser = argparse.ArgumentParser(description="kiseki UmaConn Agent（地方競馬データ取得）")
    parser.add_argument(
        "--mode",
        choices=["setup", "daily", "recent", "realtime", "retry", "fetch-results"],
        default="daily",
        help=(
            "動作モード: "
            "setup=2005年から全データ取得, "
            "daily=昨日から取得, "
            "recent=指定年以降を取得, "
            "realtime=オッズをポーリング（0B31のみ）, "
            "retry=ペンディングキューをリトライ, "
            "fetch-results=指定日の成績を0B12で取得（1回実行して終了）"
        ),
    )
    parser.add_argument(
        "--from-year",
        type=int,
        default=2023,
        metavar="YEAR",
        help="recent モードで取得する開始年 (default: 2023, 例: --from-year 2010)",
    )
    parser.add_argument(
        "--fetch-date",
        type=str,
        default=None,
        metavar="YYYYMMDD",
        help="fetch-results モードで結果を取得する対象日 (省略時は今日)",
    )
    args = parser.parse_args()

    logger.info(f"kiseki UmaConn Agent starting (mode={args.mode})")
    logger.info(f"Backend URL: {BACKEND_URL}")
    logger.info(f"Data dir: {DATA_DIR}")
    logger.info(f"Timeout (NVOpen): {_calc_timeout_seconds()} 秒")

    # 起動時に常にペンディングリトライ（retry モード以外でも）
    retry_pending(PENDING_DIR, BACKEND_URL, API_KEY)

    if args.mode == "retry":
        # リトライのみで終了
        return

    nv = init_umaconn()

    if args.mode == "setup":
        report_status("running", mode="setup", message="Starting UmaConn setup mode")
        run_setup(nv)
        report_status("done", message="UmaConn setup completed.")
        logger.info("setup モード完了。終了します。")

    elif args.mode == "daily":
        report_status("running", mode="daily", message="Starting UmaConn daily fetch")
        run_daily_fetch(nv)
        report_status("done", message="UmaConn daily fetch completed.")
        logger.info("daily モード完了。終了します。")

    elif args.mode == "recent":
        report_status(
            "running", mode="recent",
            message=f"Starting UmaConn recent mode ({args.from_year}+)",
        )
        run_recent(nv, from_year=args.from_year)
        report_status("done", message=f"UmaConn recent completed (from_year={args.from_year}).")
        logger.info("recent モード完了。終了します。")

    elif args.mode == "realtime":
        report_status("running", mode="realtime", message="Starting UmaConn realtime monitor")
        run_realtime_monitor(nv)
        report_status("done", message="UmaConn realtime monitor stopped.")

    elif args.mode == "fetch-results":
        target_date = args.fetch_date or datetime.now().strftime("%Y%m%d")
        logger.info(f"=== FETCH-RESULTS MODE: {target_date} の成績を取得 ===")
        race_keys = _fetch_today_race_keys(target_date)
        if not race_keys:
            logger.warning(f"{target_date} のレースキーが取得できませんでした")
        else:
            logger.info(f"レースキー: {len(race_keys)} 件")
            all_results: list[dict] = []
            for i, race_key in enumerate(race_keys):
                records = fetch_realtime_data(nv, RT_RESULT, race_key)
                recs = [r for r in records if r.get("rec_id") in ("RA", "SE", "HR")]
                if recs:
                    logger.info(f"  [{i+1}/{len(race_keys)}] {race_key}: {len(recs)} 件")
                    all_results.extend(recs)
                else:
                    logger.debug(f"  [{i+1}/{len(race_keys)}] {race_key}: データなし")
            if all_results:
                ra_se, hr = _split_race_hr(all_results)
                if ra_se:
                    logger.info(f"成績送信: {len(ra_se)} 件 (RA/SE)")
                    post_to_backend(EP_RACES, {"records": ra_se}, BACKEND_URL, API_KEY)
                if hr:
                    logger.info(f"払戻送信: {len(hr)} 件 (HR)")
                    _post_hr_payouts(hr)
            else:
                logger.info("成績データなし（レース未確定）")
        logger.info("fetch-results モード完了。終了します。")


if __name__ == "__main__":
    main()
