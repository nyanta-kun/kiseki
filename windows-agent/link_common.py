"""JV-Link/UmaConn 共通ユーティリティ

jvlink_agent.py と umaconn_agent.py で共有するロジック。
グローバル変数に依存せず、各関数が必要なパラメータを引数で受け取る。
"""

import json
import logging
import logging.handlers
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ログハンドラ
# ---------------------------------------------------------------------------

# エージェントは常駐かつ 30 秒周期でログを書くため、素の FileHandler だと
# 際限なく肥大する（2026-08-06 実測: umaconn_agent.log が 195MB）。
LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_BACKUP_COUNT = 5
# ローテーション失敗後、次に試すまでの待ち時間（秒）
LOG_ROLLOVER_RETRY_INTERVAL = 300.0


class SharedFileRotatingHandler(logging.handlers.RotatingFileHandler):
    """他プロセスとログファイルを共有していても壊れない RotatingFileHandler。

    Windows では他プロセスが開いているファイルを ``os.rename`` できず、
    ローテーションが ``PermissionError`` (WinError 32) になる。
    本プロジェクトのログは**常に複数プロセスで共有されている**:

      - ``umaconn_agent.log`` — realtime 常駐 + 5分おきの fetch-results
      - ``jvlink_agent.log``  — realtime 常駐 + 一発実行モード (weekly-preview 等)

    素の RotatingFileHandler だと、この失敗が ``handleError`` 経由で
    stderr に延々と出るうえ、``shouldRollover`` は真のままなので
    **1行ごとに rename を試みる**（失敗し続けるだけの syscall が毎行走る）。

    そこで失敗は握りつぶして追記を続け、次の試行まで間隔を空ける。
    上限を一時的に超えるが、EOD cleanup 後など単独で開いている瞬間に必ず成功する。
    ログを1行も落とさないことを、上限の厳密さより優先する。
    """

    def __init__(self, *args, retry_interval: float = LOG_ROLLOVER_RETRY_INTERVAL, **kwargs):
        super().__init__(*args, **kwargs)
        self._retry_interval = retry_interval
        self._next_rollover_attempt = 0.0

    def shouldRollover(self, record):  # noqa: N802 - logging の命名に合わせる
        """サイズ超過でも、直近に失敗していたら間隔が空くまで試さない。"""
        if time.time() < self._next_rollover_attempt:
            return False
        return super().shouldRollover(record)

    def doRollover(self):  # noqa: N802 - logging の命名に合わせる
        """ローテーションする。他プロセスが掴んでいる場合は諦めて追記を続ける。"""
        try:
            super().doRollover()
            self._next_rollover_attempt = 0.0
        except OSError:
            # 退避に失敗した = 他プロセスが開いている。ストリームが閉じられた
            # 可能性があるので開き直してから追記を続ける。
            self._next_rollover_attempt = time.time() + self._retry_interval
            if self.stream is None or self.stream.closed:
                self.stream = self._open()


def rotating_log_handler(
    path: str | Path,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> logging.Handler:
    """サイズローテーション付きのファイルログハンドラを返す。

    既に上限を超えているファイルは、最初のログ出力時に .1 へ退避される。

    Args:
        path: ログファイルのパス
        max_bytes: 1ファイルあたりの上限バイト数
        backup_count: 保持する世代数

    Returns:
        設定済みの SharedFileRotatingHandler
    """
    return SharedFileRotatingHandler(
        str(path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# JVOpen / NVOpen ブロック監視
# ---------------------------------------------------------------------------

class BlockingCallGuard:
    """COM 呼び出しがハングしたらプロセスごと落とすウォッチドッグ。

    JVOpen は COM レベルでブロックするため、呼び出しスレッドからは中断できない。
    別スレッドから ``os._exit`` するしか回収手段がない。

    ``os._exit`` を使うのは意図的。``sys.exit`` は呼び出しスレッドの例外なので
    COM でブロックしているスレッドには届かず、``atexit`` / ``finally`` を走らせると
    JV-Link / UmaConn が DLL detach でモーダルダイアログを出しうる。

    Why: 2026-08-06 に ``jvlink_agent.py --mode weekly-preview`` の JVOpen が
    23.3 時間返らず、JV-Link は同時1接続のみのため 4 時間おきの
    ``jvlink_historical`` が丸2日間 1 ファイルも取得できなかった。
    realtime ループにはウォッチドッグがあったが、一発実行モードには無かった。

    Example:
        with BlockingCallGuard("JVOpen(RACE)", 3600, logger):
            rc = jv.JVOpen(...)
    """

    def __init__(
        self,
        label: str,
        timeout: float,
        log: logging.Logger,
        heartbeat_interval: float = 30.0,
    ) -> None:
        """
        Args:
            label: ログに出す呼び出し名
            timeout: この秒数を超えたらプロセスを強制終了する（0以下で無効）
            log: ログ出力先
            heartbeat_interval: 経過時間をログに出す間隔（秒）
        """
        self._label = label
        self._timeout = timeout
        self._log = log
        self._interval = heartbeat_interval
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "BlockingCallGuard":
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._done.set()

    def _watch(self) -> None:
        start = time.time()
        while not self._done.wait(timeout=self._interval):
            elapsed = time.time() - start
            if 0 < self._timeout <= elapsed:
                self._log.error(
                    f"{self._label} が {int(elapsed)}秒 返りません "
                    f"(上限 {int(self._timeout)}秒) → プロセスを強制終了します"
                )
                # ログを失わないよう明示的に flush してから落とす
                for h in logging.getLogger().handlers:
                    try:
                        h.flush()
                    except Exception:  # noqa: BLE001 - 終了直前なので握りつぶす
                        pass
                os._exit(1)
            self._log.info(f"  {self._label} 待機中... 経過={int(elapsed)}秒")


# ---------------------------------------------------------------------------
# JVRead バッファ正規化
# ---------------------------------------------------------------------------

def _normalize_jvread(raw: str) -> str:
    """win32com が返す JVRead バッファを「1バイト = 1 Python文字」形式に正規化する。

    win32com の COM BSTR 機構は SJIS バイト列を Unicode に変換して返すため、
    全角文字（2 SJIS バイト）が 1 Python 文字に縮む。
    これにより JVDF 仕様書の 1-indexed バイト位置とズレが生じる。

    この関数は:
      1. raw を CP932（SJIS）バイト列に re-encode
      2. Latin-1 として re-decode → 1 バイト = 1 Python 文字

    これでパーサーの 1-indexed バイト位置がそのまま Python 文字列インデックスと一致する。
    漢字フィールドは引き続き _decode() で CP932 → Unicode に変換して読む。

    Args:
        raw: JVRead が返した Python 文字列（COM BSTR 経由で Unicode 変換済み）

    Returns:
        1バイト = 1文字 の Latin-1 文字列
    """
    try:
        return raw.encode("cp932").decode("latin-1")
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        logger.warning(f"_normalize_jvread fallback: {e}")
        return raw


# ---------------------------------------------------------------------------
# バックエンドへの HTTP POST
# ---------------------------------------------------------------------------

def post_to_backend(
    endpoint: str,
    data: dict,
    backend_url: str,
    api_key: str,
    timeout: int = 120,
) -> bool:
    """Mac側FastAPIにデータをPOSTする。

    Args:
        endpoint: APIエンドポイントのパス（例: "/api/import/races"）
        data: POSTするJSONペイロード
        backend_url: バックエンドのベースURL（例: "http://hostname:8000"）
        api_key: X-API-Key ヘッダーに設定するAPIキー
        timeout: リクエストタイムアウト秒数（デフォルト: 120）

    Returns:
        POSTが成功した場合 True、失敗した場合 False
    """
    try:
        resp = requests.post(
            f"{backend_url}{endpoint}",
            json=data,
            # Connection: close でプール再利用を防ぎ stale SSL 接続 SSLError を回避
            headers={"X-API-Key": api_key, "Connection": "close"},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True
        else:
            logger.warning(f"POST {endpoint} failed: {resp.status_code} {resp.text}")
            return False
    except requests.exceptions.Timeout:
        logger.error(f"POST timeout ({timeout}s): {endpoint}")
        return False
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Backend unreachable: {backend_url} ({type(e).__name__})")
        return False
    except Exception as e:
        logger.error(f"POST error: {e}")
        return False


# ---------------------------------------------------------------------------
# バッチ分割 POST
# ---------------------------------------------------------------------------

def _post_in_batches(
    endpoint: str,
    records: list[dict],
    batch_size: int,
    backend_url: str,
    api_key: str,
    pending_dir: Path,
) -> None:
    """レコードを batch_size 件ずつ分割してPOSTする。

    失敗したバッチはペンディングキューへ保存する。

    Args:
        endpoint: APIエンドポイントのパス（例: "/api/import/races"）
        records: 送信するレコードのリスト
        batch_size: 1回のPOSTに含めるレコード数
        backend_url: バックエンドのベースURL
        api_key: X-API-Key ヘッダーに設定するAPIキー
        pending_dir: ペンディングファイルの保存ディレクトリ
    """
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        ok = post_to_backend(endpoint, {"records": batch}, backend_url, api_key)
        if ok:
            logger.info(f"  POST {endpoint} batch[{i}:{i+batch_size}] -> OK")
        else:
            logger.warning(f"  POST {endpoint} batch[{i}:{i+batch_size}] -> NG (ペンディング保存)")
            save_pending(endpoint, batch, pending_dir)


# ---------------------------------------------------------------------------
# ローカルキャッシュ
# ---------------------------------------------------------------------------

def _cache_key(dataspec: str, from_time: str, option: int) -> str:
    """キャッシュファイルのベースキー文字列を返す。

    Args:
        dataspec: JV-Link データ種別ID（例: "RACE"）
        from_time: 取得開始日時文字列（例: "20230101000000"）
        option: JVOpen オプション値（1/2/3）

    Returns:
        キャッシュキー文字列
    """
    return f"{dataspec}_{from_time}_{option}"


def _cache_path(dataspec: str, from_time: str, option: int, cache_dir: Path) -> Path:
    """キャッシュファイルのパスを返す。

    Args:
        dataspec: JV-Link データ種別ID
        from_time: 取得開始日時文字列
        option: JVOpen オプション値
        cache_dir: キャッシュファイルを格納するディレクトリ

    Returns:
        キャッシュファイルのPath
    """
    return cache_dir / f"{_cache_key(dataspec, from_time, option)}.jsonl"


def save_cache(
    dataspec: str,
    from_time: str,
    option: int,
    records: list[dict],
    cache_dir: Path,
) -> None:
    """取得レコードをローカルJSONLキャッシュへ保存する。

    Args:
        dataspec: JV-Link データ種別ID
        from_time: 取得開始日時文字列
        option: JVOpen オプション値
        records: 保存するレコードのリスト
        cache_dir: キャッシュファイルを格納するディレクトリ
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(dataspec, from_time, option, cache_dir)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info(f"[cache] saved {len(records)} records -> {path.name}")


def load_cache(
    dataspec: str,
    from_time: str,
    option: int,
    cache_dir: Path,
) -> list[dict] | None:
    """キャッシュが存在すればレコードリストを返す。なければ None を返す。

    Args:
        dataspec: JV-Link データ種別ID
        from_time: 取得開始日時文字列
        option: JVOpen オプション値
        cache_dir: キャッシュファイルを格納するディレクトリ

    Returns:
        キャッシュが存在する場合はレコードのリスト、存在しない場合は None
    """
    path = _cache_path(dataspec, from_time, option, cache_dir)
    if not path.exists():
        return None
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    logger.info(f"[cache] loaded {len(records)} records from {path.name}")
    return records


# ---------------------------------------------------------------------------
# ペンディングキュー（POST失敗分の保存・リトライ）
# ---------------------------------------------------------------------------

def _pending_dir_for(endpoint: str, pending_dir: Path) -> Path:
    """エンドポイント別のペンディングサブディレクトリを返す。

    Args:
        endpoint: APIエンドポイントのパス（例: "/api/import/races"）
        pending_dir: ペンディングファイルのルートディレクトリ

    Returns:
        エンドポイント別のサブディレクトリPath
    """
    safe = endpoint.lstrip("/").replace("/", "_")
    return pending_dir / safe


def save_pending(
    endpoint: str,
    records: list[dict],
    pending_dir: Path,
) -> Path:
    """POST失敗レコードをペンディングキューへ保存する。

    Args:
        endpoint: APIエンドポイントのパス（例: "/api/import/races"）
        records: 保存するレコードのリスト
        pending_dir: ペンディングファイルのルートディレクトリ

    Returns:
        保存したファイルのPath
    """
    d = _pending_dir_for(endpoint, pending_dir)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = d / f"{ts}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.warning(f"[pending] saved {len(records)} records -> {path}")
    return path


def load_pending_all(
    pending_dir: Path,
) -> list[tuple[str, Path, list[dict]]]:
    """全ペンディングファイルを読み込む。

    Args:
        pending_dir: ペンディングファイルのルートディレクトリ

    Returns:
        [(endpoint_str, file_path, records), ...] のリスト
    """
    if not pending_dir.exists():
        return []
    result = []
    for ep_dir in sorted(pending_dir.iterdir()):
        if not ep_dir.is_dir():
            continue
        # ディレクトリ名から元のエンドポイントを復元: api_import_races -> /api/import/races
        endpoint = "/" + ep_dir.name.replace("_", "/")
        for jsonl_file in sorted(ep_dir.glob("*.jsonl")):
            records = []
            with jsonl_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            if records:
                result.append((endpoint, jsonl_file, records))
    return result


def retry_pending(
    pending_dir: Path,
    backend_url: str,
    api_key: str,
    batch_size: int = 200,
) -> None:
    """ペンディングキューをすべてリトライする。成功したファイルは削除する。

    大きなペンディングファイルは batch_size ずつ分割して送信する。

    Args:
        pending_dir: ペンディングファイルのルートディレクトリ
        backend_url: バックエンドのベースURL
        api_key: X-API-Key ヘッダーに設定するAPIキー
        batch_size: 1リクエストに含めるレコード数上限
    """
    items = load_pending_all(pending_dir)
    if not items:
        logger.info("[pending] ペンディングキューは空です")
        return

    logger.info(f"[pending] {len(items)} ファイルをリトライします")

    for endpoint, path, records in items:
        failed: list[dict] = []
        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            ok = post_to_backend(endpoint, {"records": batch}, backend_url, api_key)
            if not ok:
                failed.extend(batch)
        if not failed:
            path.unlink()
            logger.info(f"[pending] OK -> 削除: {path.name} ({len(records)} records, {endpoint})")
        else:
            # 失敗分のみファイルを上書きして残す
            with path.open("w", encoding="utf-8") as f:
                for rec in failed:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            logger.warning(
                f"[pending] NG -> 残留: {path.name} ({len(failed)}/{len(records)} records, {endpoint})"
            )


# ---------------------------------------------------------------------------
# ステータス報告
# ---------------------------------------------------------------------------

def report_status(
    status: str,
    mode: str | None,
    message: str,
    progress: dict | None,
    backend_url: str,
    api_key: str,
) -> None:
    """バックエンドへ現在のステータスをPOSTする。

    Args:
        status: "running" | "idle" | "error" | "done"
        mode: "setup" | "daily" | "realtime" | None
        message: 状態の説明
        progress: 任意の進捗情報（None の場合は空dict）
        backend_url: バックエンドのベースURL
        api_key: X-API-Key ヘッダーに設定するAPIキー
    """
    payload = {
        "status": status,
        "mode": mode,
        "message": message,
        "progress": progress or {},
    }
    try:
        requests.post(
            f"{backend_url}/api/agent/status",
            json=payload,
            headers={"X-API-Key": api_key},
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"Status report failed (non-critical): {e}")
