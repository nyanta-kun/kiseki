"""払戻データ（HR レコード）バックフィルスクリプト

race_payouts / race_results.place_odds が未収集であるため、
JV-Link RACE DataSpec から HR レコードのみを再取得して DB へ反映する。

スキップ戦略:
  - PAYOUT_completed.txt に登録済み → JVSkip（処理済み）
  - RACE_completed.txt に登録済み かつ ファイル名がH始まりでない → JVSkip（RA/SE系）
  - H始まりのファイル（HR払戻） → 読み込んでHRレコードをPOST

これにより RA/SE ファイルはJVSkipで高速スキップ、
H系ファイルのみ読み込んで払戻を収集する。

使用方法:
    python payout_backfill.py [--from-year YYYY]

    --from-year: 取得開始年（デフォルト: 2024）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# ロギング設定
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("payout_backfill.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数（jvlink_agent.py と合わせる）
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
COMPLETED_DIR = DATA_DIR / "completed"
COMPLETED_DIR.mkdir(parents=True, exist_ok=True)

# 払戻専用の完了ファイル（RACE_completed.txt とは独立）
PAYOUT_COMPLETED_FILE = COMPLETED_DIR / "PAYOUT_completed.txt"

DATASPEC_RACE = "RACE"

# ---------------------------------------------------------------------------
# バックエンド設定（jvlink_agent.py と同じ）
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BASE_DIR.parent / ".env")
except ImportError:
    pass

import os
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("AGENT_API_KEY", "")

# ---------------------------------------------------------------------------
# 完了ファイル管理
# ---------------------------------------------------------------------------

def load_payout_completed() -> set[str]:
    if not PAYOUT_COMPLETED_FILE.exists():
        return set()
    return set(PAYOUT_COMPLETED_FILE.read_text(encoding="utf-8").splitlines())


def mark_payout_completed(filename: str) -> None:
    with PAYOUT_COMPLETED_FILE.open("a", encoding="utf-8") as f:
        f.write(filename + "\n")


def load_race_completed() -> set[str]:
    """既存の RACE_completed.txt を読み込む（RA/SE ファイルのスキップに使用）。"""
    race_completed_file = COMPLETED_DIR / "RACE_completed.txt"
    if not race_completed_file.exists():
        return set()
    return set(race_completed_file.read_text(encoding="utf-8").splitlines())


# ---------------------------------------------------------------------------
# バックエンド送信（jvlink_agent.py の post_to_backend と同等）
# ---------------------------------------------------------------------------
import urllib.request
import urllib.error


def post_to_backend(endpoint: str, payload: dict, timeout: int = 300) -> bool:
    url = BACKEND_URL.rstrip("/") + endpoint
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        logger.error(f"POST {endpoint} HTTP {e.code}: {e.read()[:200]}")
        return False
    except Exception as e:
        logger.error(f"POST {endpoint} 失敗: {e}")
        return False


# ---------------------------------------------------------------------------
# HR レコード送信
# ---------------------------------------------------------------------------

PAYOUT_BATCH_SIZE = 50  # 1回の POST に含める HR レコード数


def _post_hr_payouts(hr_records: list[dict]) -> None:
    if not hr_records:
        return

    try:
        from jvlink_parser import parse_hr
    except ImportError:
        logger.warning("jvlink_parser.parse_hr が利用できません。スキップします。")
        return

    parsed = []
    for rec in hr_records:
        result = parse_hr(rec.get("data", ""))
        if result:
            parsed.append(result)

    if not parsed:
        return

    # バッチ分割して POST
    ok_count = 0
    for i in range(0, len(parsed), PAYOUT_BATCH_SIZE):
        batch = parsed[i : i + PAYOUT_BATCH_SIZE]
        ok = post_to_backend("/api/import/payouts", {"records": batch})
        if ok:
            ok_count += len(batch)
        else:
            logger.warning(f"  POST /api/import/payouts batch[{i}:{i+PAYOUT_BATCH_SIZE}] -> NG")

    logger.info(f"  POST /api/import/payouts {ok_count}/{len(parsed)} 件 -> 完了")


# ---------------------------------------------------------------------------
# JV-Link 取得メイン（HR レコードのみ）
# ---------------------------------------------------------------------------

def run_payout_backfill(jv, from_year: int = 2024, option: int = 1) -> None:
    from_time = f"{from_year}0101000000"
    logger.info(f"=== 払戻バックフィル開始: {from_year}年以降の HR レコードを取得 (option={option}) ===")

    payout_completed = load_payout_completed()
    race_completed = load_race_completed()
    logger.info(f"[payout_completed] 処理済みファイル: {len(payout_completed)} 件")
    logger.info(f"[race_completed] 参照ファイル（RA/SEスキップ用）: {len(race_completed)} 件")

    total = {"hr_files": 0, "hr_records": 0, "skipped": 0}

    def on_file_done(filename: str, file_records: list[dict]) -> None:
        if filename in payout_completed:
            total["skipped"] += 1
            return

        # HR レコードのみ抽出
        hr = [r for r in file_records if r.get("rec_id") == "HR"]

        if not hr:
            # HR なし → 完了マークだけ付ける
            mark_payout_completed(filename)
            return

        logger.info(f"  [{filename}] HR {len(hr)} 件 → 払戻DB反映")
        _post_hr_payouts(hr)
        total["hr_records"] += len(hr)
        total["hr_files"] += 1
        mark_payout_completed(filename)
        logger.info(
            f"  [{filename}] 完了 (累計: HR {total['hr_files']} ファイル / {total['hr_records']} 件)"
        )

    def skip_fn(filename: str) -> bool:
        """スキップ判定:
        1. PAYOUT_completed: 既に払戻処理済み → スキップ
        2. RACE_completed かつ H始まりでない（RA/SE系）→ スキップ
        3. H始まりファイル（HR払戻）→ 読み込む
        """
        if filename in payout_completed:
            return True
        # H始まりでないファイルは RA/SE 系なので RACE_completed に入っていればスキップ
        if not filename.startswith("H") and filename in race_completed:
            return True
        return False

    # option=1（通常モード）: JV-Linkローカルキャッシュを使用。
    # option=3（セットアップ）: JRA-VANから全データを再ダウンロード。消費済みファイルも再取得可能。
    # H始まりでないファイル(RA/SE)はskip_fnでJVSkipするため高速。
    opt_label = "セットアップ/全再ダウンロード" if option == 3 else "通常/ローカルキャッシュ優先"
    logger.info(f"JVOpen RACE from={from_time} option={option} ({opt_label})...")

    _jvopen_done = threading.Event()

    def _heartbeat():
        start = time.time()
        while not _jvopen_done.is_set():
            _jvopen_done.wait(timeout=30)
            if not _jvopen_done.is_set():
                elapsed = int(time.time() - start)
                logger.info(f"JVOpen 待機中... {elapsed}秒経過")

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()

    result = jv.JVOpen(DATASPEC_RACE, from_time, option, 0, 0, "")
    _jvopen_done.set()

    if isinstance(result, tuple):
        rc = result[0]
    else:
        rc = result

    logger.info(f"JVOpen rc={rc}")
    if rc < 0:
        logger.error(f"JVOpen エラー: rc={rc}")
        return

    # JVRead ループ
    # 戻り値: (rc, buf, size, filename) の4要素タプル
    file_records: list[dict] = []
    current_file = ""
    skip_current = False  # 現在ファイルをスキップ中フラグ
    read_count = 0
    skip_count = 0
    wait_count = 0

    while True:
        r = jv.JVRead("", 256000, "")
        rc2 = r[0]

        if rc2 == 0:  # EOF
            if current_file and not skip_current:
                on_file_done(current_file, file_records)
            logger.info(f"JVRead 完了: 読込={read_count} スキップファイル={skip_count}")
            break
        elif rc2 == -1:  # ファイル切り替わり
            # 前ファイルの処理
            if current_file and not skip_current:
                on_file_done(current_file, file_records)
            # 新ファイル開始: jvlink_agent.py と同様に r[3] → r[2] のフォールバック
            # JVRead 戻り値は (rc, buf, size, filename) の4要素だが、
            # 実装によっては (rc, buf, filename) の3要素になる場合がある
            new_file = r[3] if len(r) > 3 else (r[2] if len(r) > 2 else "")
            if new_file and hasattr(new_file, "strip"):
                new_file = new_file.strip()
            current_file = new_file
            file_records = []
            wait_count = 0
            # 新ファイルをスキップすべきか判定
            skip_current = skip_fn(current_file)
            if skip_current:
                rc_skip = jv.JVSkip()
                if rc_skip == 0:
                    skip_count += 1
                    current_file = ""
                    skip_current = False
                else:
                    logger.debug(f"JVSkip 失敗(rc={rc_skip}): 読み捨てモード")
            continue
        elif rc2 == -3:  # ダウンロード中
            wait_count += 1
            if wait_count % 10 == 0:
                logger.info(f"ダウンロード待機中... ({wait_count * 10}秒)")
            time.sleep(1)
            continue
        elif rc2 < -1:  # エラー
            logger.error(f"JVRead エラー: rc={rc2}, ファイル={current_file}")
            break

        # 正常レコード
        if skip_current:
            continue  # スキップ中は蓄積しない

        read_count += 1
        # r[1]=buf(文字列), r[2]=実バイト数
        buf_data = r[1] if r[1] else ""
        rec_id = buf_data[:2] if len(buf_data) >= 2 else ""
        file_records.append({"rec_id": rec_id, "data": buf_data})

    try:
        jv.JVClose()
    except Exception:
        pass

    logger.info(
        f"=== 払戻バックフィル完了: {total['hr_files']} ファイル / "
        f"{total['hr_records']} HR レコード / {total['skipped']} スキップ ==="
    )


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="払戻データ HR バックフィル")
    parser.add_argument("--from-year", type=int, default=2024, help="取得開始年 (default: 2024)")
    parser.add_argument("--option", type=int, default=1, choices=[1, 3],
                        help="JVOpen option: 1=通常(キャッシュ), 3=セットアップ(全再DL) (default: 1)")
    args = parser.parse_args()

    logger.info(f"払戻バックフィル開始: from_year={args.from_year}, option={args.option}")

    try:
        import win32com.client
        jv = win32com.client.Dispatch("JVDTLab.JVLink.1")

        # JV-Link 初期化
        rc = jv.JVInit("UNKNOWN")
        if rc != 0:
            logger.error(f"JVInit エラー: rc={rc}")
            sys.exit(1)

        logger.info("JVLink 初期化 OK")
        run_payout_backfill(jv, from_year=args.from_year, option=args.option)

    except ImportError:
        logger.error("win32com.client が見つかりません。Windows Python 環境で実行してください。")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"予期しないエラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
