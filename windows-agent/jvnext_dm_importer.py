"""JRA-VAN NEXT DM指数インポーター

1403/{date}{course}{race_no}.dat ファイルを読み取り、
タイム型DM・対戦型DM指数を Backend API に POST する。

使用方法:
    python jvnext_dm_importer.py                           # 今日の日付を自動使用
    python jvnext_dm_importer.py --date 20260425           # 指定日
    python jvnext_dm_importer.py --start 20260412 --end 20260427  # 日付範囲バッチ
    python jvnext_dm_importer.py --all                     # 永続ストアの全日付
    python jvnext_dm_importer.py --retry-failed            # 前回失敗レースのみ再実行
    python jvnext_dm_importer.py --race-ids 2026042605020201,2026042608030201
    python jvnext_dm_importer.py --dry-run                 # POSTせず内容表示のみ
    python jvnext_dm_importer.py --reset-progress          # 進捗クリア後に全再インポート
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import time
import zlib
from datetime import datetime
from pathlib import Path

import requests

# WindowsシステムCA証明書をPython SSLに注入（Let's Encrypt E8等の新CAに対応）
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

LOG_FILE = r"C:\kiseki\windows-agent\jvnext_dm_importer.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

CACHE_ROOT = Path(r"C:\Users\ysuzuki\AppData\Local\JRA-VAN\NEXT\cache")
PERSISTENT_STORE = Path(r"C:\kiseki\data\dm_1403")
PROGRESS_FILE = Path(r"C:\kiseki\data\dm_import_progress.json")

RECORD_LEN = 25
LINE1_HEADER_LEN = 23

BACKEND_URL = os.environ.get("BACKEND_URL", "https://api.galloplab.com")
API_KEY = os.environ.get("CHANGE_NOTIFY_API_KEY", "")


# ---------------------------------------------------------------------------
# 進捗管理
# ---------------------------------------------------------------------------

def load_progress() -> dict[str, str]:
    """進捗ファイルを読み込む。{"jravan_race_id": "ok"|"failed"}"""
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_progress(progress: dict[str, str]) -> None:
    """進捗ファイルを書き込む（アトミック更新）。"""
    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PROGRESS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PROGRESS_FILE)
    except Exception as e:
        logger.warning(f"進捗ファイル書き込み失敗: {e}")


# ---------------------------------------------------------------------------
# ファイル解析
# ---------------------------------------------------------------------------

def load_1403_file(path: Path) -> dict[int, dict[str, float | None]]:
    """1403 DM指数ファイルを読み込み、馬番→DM値のdictを返す。

    Returns:
        {horse_number: {"jvan_time_dm": 81.0, "jvan_battle_dm": 80.7}}
    """
    try:
        raw = path.read_bytes()
        dec = zlib.decompress(raw)
        text = dec.decode("cp932", errors="replace")
        lines = text.split("\r\n")
    except Exception as e:
        logger.error(f"Failed to decode {path}: {e}")
        return {}

    if len(lines) < 2:
        logger.warning(f"Unexpected line count in {path}: {len(lines)}")
        return {}

    # 先頭1文字が更新回数（1,2,3...）の行から最終更新版を使用。upd=0は別種別データ。
    data_lines = [(int(l[0]), l) for l in lines if l and l[0].isdigit() and int(l[0]) >= 1]
    if not data_lines:
        logger.warning(f"No DM data lines in {path}")
        return {}
    _, latest_line = max(data_lines, key=lambda x: x[0])

    result: dict[int, dict[str, float | None]] = {}
    horse_number = 1
    pos = LINE1_HEADER_LEN

    while pos + RECORD_LEN <= len(latest_line):
        chunk = latest_line[pos : pos + RECORD_LEN]
        if not chunk.strip():
            break
        try:
            time_raw = chunk[0:4].strip()
            battle_raw = chunk[4:8].strip()
            jvan_time_dm: float | None = int(time_raw) / 10.0 if time_raw else None
            # "0000" はデータなし（スクラッチ馬等）
            jvan_battle_dm: float | None = (
                int(battle_raw) / 10.0 if battle_raw and battle_raw != "0000" else None
            )
        except (ValueError, IndexError):
            jvan_time_dm = None
            jvan_battle_dm = None

        if jvan_time_dm is not None or jvan_battle_dm is not None:
            result[horse_number] = {
                "jvan_time_dm": jvan_time_dm,
                "jvan_battle_dm": jvan_battle_dm,
            }
        horse_number += 1
        pos += RECORD_LEN

    return result


# ---------------------------------------------------------------------------
# ファイル収集
# ---------------------------------------------------------------------------

def _copy_to_persistent_store(src_path: Path) -> None:
    """1403ファイルをlive cacheから永続ストアにコピーする。"""
    try:
        PERSISTENT_STORE.mkdir(parents=True, exist_ok=True)
        dst = PERSISTENT_STORE / src_path.name
        if not dst.exists():
            shutil.copy2(src_path, dst)
    except Exception as e:
        logger.debug(f"永続ストアへのコピー失敗: {src_path.name}: {e}")


def discover_dates() -> list[str]:
    """永続ストア内の全日付（YYYYMMDD）を取得する。"""
    dates: set[str] = set()
    for p in PERSISTENT_STORE.glob("1403????????*.dat"):
        if len(p.stem) == 16:
            dates.add(p.stem[4:12])
    return sorted(dates)


def collect_files_for_dates(dates: list[str], course_filter: str | None = None) -> list[Path]:
    """指定日付リストの全1403ファイルを収集し、live cacheのものは永続ストアにコピーする。"""
    seen_stems: set[str] = set()
    dm_files: list[Path] = []

    for date in dates:
        for search_dir in [CACHE_ROOT / "1403", PERSISTENT_STORE]:
            if not search_dir.exists():
                continue
            for p in sorted(search_dir.glob(f"1403{date}*.dat")):
                if p.stem not in seen_stems:
                    if len(p.stem) == 16:
                        cc = p.stem[12:14]
                        if course_filter and cc != course_filter:
                            continue
                    seen_stems.add(p.stem)
                    dm_files.append(p)
                    if search_dir == CACHE_ROOT / "1403":
                        _copy_to_persistent_store(p)

    return dm_files


# ---------------------------------------------------------------------------
# API連携
# ---------------------------------------------------------------------------

def fetch_race_id_map(date: str) -> dict[tuple[str, str], str]:
    """Backend API から指定日のコース+レース番号→jravan_race_id マップを取得する。

    Returns:
        {("05", "01"): "2026042605020201", ...}
    """
    url = f"{BACKEND_URL}/api/races?date={date}"
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=15, headers={"Connection": "close"})
            resp.raise_for_status()
            data = resp.json()
            races = data.get("races", data) if isinstance(data, dict) else data
            result: dict[tuple[str, str], str] = {}
            for r in races:
                rid = r.get("jravan_race_id", "")
                if len(rid) == 16:
                    result[(rid[8:10], rid[14:16])] = rid
            logger.info(f"  [API] {date}: {len(result)}レース取得")
            return result
        except Exception as e:
            if attempt < 2:
                logger.warning(f"API race_id 取得リトライ({attempt + 1}/3): {e}")
                time.sleep(3)
            else:
                logger.warning(f"API race_id 取得失敗 date={date}: {e}")
    return {}


def post_race_records(jravan_race_id: str, records: list[dict]) -> dict:
    """1レース分のDM指数をBackend APIにPOSTする（3回リトライ）。"""
    url = f"{BACKEND_URL}/api/import/jvan_dm"
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json", "Connection": "close"}
    payload = {"records": records}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < 2:
                logger.warning(f"  POST リトライ({attempt + 1}/3) {jravan_race_id}: {e}")
                time.sleep(3)
            else:
                raise


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def run_import(
    dm_files: list[Path],
    progress: dict[str, str],
    skip_ok: bool = True,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """DM指数インポートを実行する。

    Returns:
        (ok_count, failed_count, skipped_count)
    """
    if not dm_files:
        logger.info("対象ファイルなし")
        return 0, 0, 0

    # 日付ごとにrace_idマップをキャッシュ（同一日付の複数ファイルで再利用）
    race_id_map_cache: dict[str, dict[tuple[str, str], str]] = {}

    ok = failed = skipped = 0

    for dm_path in dm_files:
        fname = dm_path.stem  # 例: "1403202604260501"
        if len(fname) != 16:
            logger.warning(f"ファイル名長エラー: {fname}")
            skipped += 1
            continue

        date = fname[4:12]
        course_code = fname[12:14]
        race_no_str = fname[14:16]

        # race_id解決
        if date not in race_id_map_cache:
            race_id_map_cache[date] = fetch_race_id_map(date)
        race_id_map = race_id_map_cache[date]

        jravan_race_id = race_id_map.get((course_code, race_no_str))
        if not jravan_race_id:
            logger.warning(f"  race_id解決不可: {fname} CC={course_code} R{race_no_str}")
            skipped += 1
            continue

        # 進捗チェック
        if skip_ok and progress.get(jravan_race_id) == "ok":
            skipped += 1
            continue

        dm_map = load_1403_file(dm_path)
        if not dm_map:
            logger.warning(f"  DM data なし: {dm_path.name}")
            skipped += 1
            continue

        records = [
            {
                "jravan_race_id": jravan_race_id,
                "horse_number": horse_no,
                "jvan_time_dm": v["jvan_time_dm"],
                "jvan_battle_dm": v["jvan_battle_dm"],
            }
            for horse_no, v in dm_map.items()
        ]

        if dry_run:
            logger.info(f"  [DRY-RUN] {dm_path.name} → {jravan_race_id} {len(records)}頭")
            ok += 1
            continue

        try:
            result = post_race_records(jravan_race_id, records)
            updated = result.get("updated", 0)
            logger.info(f"  {dm_path.name} → {jravan_race_id} updated={updated}")
            progress[jravan_race_id] = "ok"
            ok += 1
        except Exception as e:
            logger.error(f"  POST失敗: {jravan_race_id}: {e}")
            progress[jravan_race_id] = "failed"
            failed += 1

        # レースごとに進捗を保存（クラッシュ時も途中まで保持される）
        save_progress(progress)

    return ok, failed, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="JRA-VAN NEXT DM指数インポーター")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--date", help="対象日 YYYYMMDD（省略時は今日）")
    mode.add_argument("--start", help="開始日 YYYYMMDD（--end と組み合わせ）")
    mode.add_argument("--all", action="store_true", help="永続ストアの全日付を処理")
    mode.add_argument("--retry-failed", action="store_true", help="前回失敗レースのみ再実行")
    mode.add_argument("--race-ids", help="カンマ区切りのjravan_race_id（特定レース指定）")
    parser.add_argument("--end", help="終了日 YYYYMMDD（--start と組み合わせ）")
    parser.add_argument("--course", help="コースコード絞り込み (e.g. 05=東京)")
    parser.add_argument("--dry-run", action="store_true", help="POSTせず内容表示のみ")
    parser.add_argument("--reset-progress", action="store_true", help="進捗クリア後に全再インポート")
    args = parser.parse_args()

    progress = load_progress()

    if args.reset_progress:
        progress = {}
        save_progress(progress)
        logger.info("進捗クリア完了")

    # --- 対象ファイルの決定 ---
    if args.retry_failed:
        failed_ids = {rid for rid, st in progress.items() if st == "failed"}
        if not failed_ids:
            logger.info("失敗レコードなし（終了）")
            return
        logger.info(f"=== retry-failed: {len(failed_ids)}レース ===")
        dm_files = collect_files_for_dates(discover_dates(), args.course)
        # 失敗IDに対応するファイルのみ絞り込み（race_idマップは後で解決）
        skip_ok = False  # 失敗分を強制再実行
    elif args.race_ids:
        target_ids = set(args.race_ids.split(","))
        logger.info(f"=== race-ids指定: {len(target_ids)}レース ===")
        # 対象IDの日付を逆引き（ファイル名から）
        dates = sorted({rid[0:8] for rid in target_ids})
        dm_files = collect_files_for_dates(dates, args.course)
        skip_ok = False
    elif args.all:
        dates = discover_dates()
        if not dates:
            logger.info("永続ストアにファイルなし（終了）")
            return
        logger.info(f"=== all: {len(dates)}日付 ({dates[0]}〜{dates[-1]}) ===")
        dm_files = collect_files_for_dates(dates, args.course)
        skip_ok = True
    elif args.start:
        end = args.end or datetime.now().strftime("%Y%m%d")
        all_dates = discover_dates()
        dates = [d for d in all_dates if args.start <= d <= end]
        logger.info(f"=== {args.start}〜{end}: {len(dates)}日付 ===")
        dm_files = collect_files_for_dates(dates, args.course)
        skip_ok = True
    else:
        date = args.date or datetime.now().strftime("%Y%m%d")
        logger.info(f"=== date={date} ===")
        dm_files = collect_files_for_dates([date], args.course)
        skip_ok = True

    logger.info(f"Backend: {BACKEND_URL}")
    logger.info(f"対象ファイル: {len(dm_files)}件")

    # --retry-failed の場合は失敗IDのみに絞る
    if args.retry_failed:
        failed_ids = {rid for rid, st in progress.items() if st == "failed"}
        # ファイルから対応するrace_idを特定するためにrace_id_mapを先行取得
        dates_needed = sorted({p.stem[4:12] for p in dm_files if len(p.stem) == 16})
        race_id_map_all: dict[tuple[str, str], str] = {}
        for d in dates_needed:
            race_id_map_all.update(fetch_race_id_map(d))
        reverse_map = {v: k for k, v in race_id_map_all.items()}
        dm_files = [
            p for p in dm_files
            if len(p.stem) == 16 and
            race_id_map_all.get((p.stem[12:14], p.stem[14:16])) in failed_ids
        ]
        logger.info(f"失敗レース対象: {len(dm_files)}件")

    # --race-ids の場合もファイルを絞り込む
    if args.race_ids:
        target_ids = set(args.race_ids.split(","))
        dates_needed = sorted({p.stem[4:12] for p in dm_files if len(p.stem) == 16})
        race_id_map_all = {}
        for d in dates_needed:
            race_id_map_all.update(fetch_race_id_map(d))
        dm_files = [
            p for p in dm_files
            if len(p.stem) == 16 and
            race_id_map_all.get((p.stem[12:14], p.stem[14:16])) in target_ids
        ]
        logger.info(f"指定レース対象: {len(dm_files)}件")

    if not dm_files:
        logger.info("対象ファイルなし（終了）")
        return

    ok, failed, skipped = run_import(dm_files, progress, skip_ok=skip_ok, dry_run=args.dry_run)

    logger.info(
        f"=== 完了: ok={ok}, failed={failed}, skipped={skipped} "
        f"(進捗: {PROGRESS_FILE}) ==="
    )
    if failed:
        logger.warning(f"失敗レース {failed}件 → --retry-failed で再実行できます")


if __name__ == "__main__":
    main()
