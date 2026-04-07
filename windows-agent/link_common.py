"""JV-Link/UmaConn 共通ユーティリティ

jvlink_agent.py と umaconn_agent.py で共有するロジック。
グローバル変数に依存せず、各関数が必要なパラメータを引数で受け取る。
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


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
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True
        else:
            logger.warning(f"POST {endpoint} failed: {resp.status_code} {resp.text}")
            return False
    except requests.exceptions.ConnectionError:
        logger.error(f"Backend unreachable: {backend_url}")
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
) -> None:
    """ペンディングキューをすべて並列リトライする。成功したファイルは削除する。

    Args:
        pending_dir: ペンディングファイルのルートディレクトリ
        backend_url: バックエンドのベースURL
        api_key: X-API-Key ヘッダーに設定するAPIキー
    """
    items = load_pending_all(pending_dir)
    if not items:
        logger.info("[pending] ペンディングキューは空です")
        return

    logger.info(f"[pending] {len(items)} ファイルをリトライします (並列4)")

    def _retry_one(item: tuple) -> tuple[bool, str, int, str, Path]:
        """1ペンディングファイルをリトライする。"""
        endpoint, path, records = item
        ok = post_to_backend(endpoint, {"records": records}, backend_url, api_key)
        return ok, path.name, len(records), endpoint, path

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_retry_one, item): item for item in items}
        for future in as_completed(futures):
            ok, name, count, endpoint, path = future.result()
            if ok:
                path.unlink()
                logger.info(f"[pending] OK -> 削除: {name} ({count} records, {endpoint})")
            else:
                logger.warning(f"[pending] NG -> 残留: {name} ({count} records, {endpoint})")


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
