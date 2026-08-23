"""確定オッズ（O1〜O6 レコード）バックフィルスクリプト

蓄積系 `RACE` DataSpec には RA/SE/HR に加えて **O1〜O6（確定オッズ）** が含まれる
（`docs/jvdata-spec.md` の蓄積系データ種別ID 表）。ところが `jvlink_agent.py` の
`_filter_race_records()` が RA/SE/HR だけを残して O レコードを捨てているため、
確定オッズは一度も DB に入っていない。

一方、速報系（`JVRTOpen` の 0B31〜0B36）で集めた `keiba.odds_history` の
エキゾチックオッズは、2026-08-23 まで `EXOTIC_HEADER_SIZE` が 11 バイトずれていて
**全て壊れている**（馬番が出走頭数以内の行は三連単 1.4% / 三連複 3.0%）。
生レコードは保存していないので、壊れた列からオッズ値を復元することはできない。

→ **本スクリプトで蓄積系から確定オッズを取り直す。** 修正後のパーサ
（`backend/src/importers/odds_importer.py`）を通るので、正しい組番・オッズが入る。

`payout_backfill.py`（HR を同じ方法で回収する既存スクリプト）と同じ構造。

スキップ戦略:
  - ODDS_completed.txt に登録済み → JVSkip（処理済み）
  - RACE_completed.txt に登録済み かつ ファイル名が O 始まりでない → JVSkip（RA/SE/HR系）
  - O 始まりのファイル → 読み込んで O レコードを POST

⚠️ ファイル名の先頭文字でレコード種別を判定するのは `payout_backfill.py` の
   「H 始まり = HR」と同じ経験則。外れてもスキップが減って遅くなるだけで、
   取り込むデータは変わらない（rec_id で必ず絞っているため）。

使用方法:
    python odds_backfill.py [--from-year YYYY] [--option 1|3|4]

    --from-year: 取得開始年（デフォルト: 2024）
    --option:    1=通常(ローカルキャッシュ優先。ただし JRA 側の保持窓が1年しかないので
                 2024年まで遡るには 3 か 4 が要る)
                 3=セットアップ(全再ダウンロード・選択ダイアログ有)
                 4=セットアップ(ダイアログ無)。jvlink_agent.py の RACE 取得と同じ経路。
                   5.0.0 では 3/4 とも不可視ダイアログを出すが jvlink_dialog_guard が応答する
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from link_common import BlockingCallGuard


# ⚠️ 先行インスタンスがログを掴んでいると FileHandler が PermissionError で落ち、
#    プロセスが起動時点で死ぬ（payout_backfill.py で実際に起きて _out.txt /
#    _run2.log / _run3.log という別名ファイルが乱立した）。掴まれていたら
#    プロセスIDを付けた別名へ逃がす。
def _log_handlers() -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    for name in ("odds_backfill.log", f"odds_backfill_{os.getpid()}.log"):
        try:
            handlers.append(logging.FileHandler(name, encoding="utf-8"))
            break
        except PermissionError:
            continue
    return handlers


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers(),
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数（jvlink_agent.py / payout_backfill.py と合わせる）
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
COMPLETED_DIR = DATA_DIR / "completed"
COMPLETED_DIR.mkdir(parents=True, exist_ok=True)

# オッズ専用の完了ファイル（RACE_completed.txt / PAYOUT_completed.txt とは独立）
ODDS_COMPLETED_FILE = COMPLETED_DIR / "ODDS_completed.txt"

DATASPEC_RACE = "RACE"
ODDS_REC_IDS = ("O1", "O2", "O3", "O4", "O5", "O6")

# 1回の POST に含める O レコード数。
# O6（三連単）は 1 レコード 83,285 バイトあるので小さめにする。
ODDS_BATCH_SIZE = 20

# ⚠️ VM には .env が 2 つある。
#   C:\kiseki\.env               → BACKEND_URL=https://api.galloplab.com（到達可・200）
#   C:\kiseki\windows-agent\.env → BACKEND_URL=http://192.168.11.26:8000（**到達不可**）
# python-dotenv は既定 override=False なので、先に読んだ方が勝つ。
# payout_backfill.py と同じ順（windows-agent → 親）だと死んだURLを掴むため、
# **親（C:\kiseki\.env）を先に読む**。--backend-url で明示指定もできる。
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR.parent / ".env")
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

# 🔴 option=3/4 は JV-Link 5.0.0 で**不可視の「セットアップ」ダイアログ**を出す。
# 誰も押さないと JVOpen が永久にブロックする（2026-08-23 に #266 でエージェント経路は
# 直したが、このスクリプトは link_common を通しておらず素の JVOpen を呼んでいた）。
# BlockingCallGuard が 5 秒ごとに jvlink_dialog_guard.dismiss() を叩き、
# 上限を超えたらプロセスごと落とす。JVOpen がこの秒数を超えて返らなければ強制終了する。
JVOPEN_TIMEOUT_SEC = 3600

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("AGENT_API_KEY", "") or os.getenv("CHANGE_NOTIFY_API_KEY", "")


# ---------------------------------------------------------------------------
# 完了ファイル管理
# ---------------------------------------------------------------------------
def load_odds_completed() -> set[str]:
    if not ODDS_COMPLETED_FILE.exists():
        return set()
    return set(ODDS_COMPLETED_FILE.read_text(encoding="utf-8").splitlines())


def mark_odds_completed(filename: str) -> None:
    with ODDS_COMPLETED_FILE.open("a", encoding="utf-8") as f:
        f.write(filename + "\n")


def load_race_completed() -> set[str]:
    """既存の RACE_completed.txt（RA/SE/HR ファイルのスキップに使う）。"""
    race_completed_file = COMPLETED_DIR / "RACE_completed.txt"
    if not race_completed_file.exists():
        return set()
    return set(race_completed_file.read_text(encoding="utf-8").splitlines())


# ---------------------------------------------------------------------------
# バックエンド送信
# ---------------------------------------------------------------------------
def post_to_backend(endpoint: str, payload: dict, timeout: int = 300) -> bool:
    url = BACKEND_URL.rstrip("/") + endpoint
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
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


def _post_odds_records(odds_records: list[dict]) -> int:
    """O レコードを /api/import/odds へ送る。

    backend の `OddsImporter.import_records()` は
    `[{"rec_id": "O5", "data": "O5..."}, ...]` をそのまま受け取り、
    レコード内で jravan_race_id を引いて DB のレースに紐づける。

    Returns:
        POST に成功したレコード数
    """
    if not odds_records:
        return 0

    ok_count = 0
    for i in range(0, len(odds_records), ODDS_BATCH_SIZE):
        batch = odds_records[i : i + ODDS_BATCH_SIZE]
        # WeightRequest は date を必須にしているが、オッズの日付は
        # レコード内の開催年月日から引くので、ここでは体裁のみ合わせる。
        ok = post_to_backend("/api/import/odds", {"date": "", "records": batch})
        if ok:
            ok_count += len(batch)
        else:
            logger.warning(f"  POST /api/import/odds batch[{i}:{i + ODDS_BATCH_SIZE}] -> NG")

    return ok_count


# ---------------------------------------------------------------------------
# JV-Link 取得メイン（O レコードのみ）
# ---------------------------------------------------------------------------
def run_odds_backfill(jv, from_year: int = 2024, option: int = 1) -> None:
    from_time = f"{from_year}0101000000"
    logger.info(f"=== 確定オッズ バックフィル開始: {from_year}年以降の O1〜O6 を取得 (option={option}) ===")

    odds_completed = load_odds_completed()
    race_completed = load_race_completed()
    logger.info(f"[odds_completed] 処理済みファイル: {len(odds_completed)} 件")
    logger.info(f"[race_completed] 参照ファイル（RA/SE/HRスキップ用）: {len(race_completed)} 件")

    total = {"files": 0, "records": 0, "posted": 0, "skipped": 0}
    by_rec: dict[str, int] = {}

    def on_file_done(filename: str, file_records: list[dict]) -> None:
        if filename in odds_completed:
            total["skipped"] += 1
            return

        odds = [r for r in file_records if r.get("rec_id") in ODDS_REC_IDS]
        if not odds:
            mark_odds_completed(filename)
            return

        for r in odds:
            by_rec[r["rec_id"]] = by_rec.get(r["rec_id"], 0) + 1

        logger.info(f"  [{filename}] O レコード {len(odds)} 件 → オッズDB反映")
        posted = _post_odds_records(odds)
        total["records"] += len(odds)
        total["posted"] += posted
        total["files"] += 1
        mark_odds_completed(filename)
        logger.info(
            f"  [{filename}] 完了 (累計: {total['files']} ファイル / "
            f"{total['posted']}/{total['records']} 件)"
        )

    def skip_fn(filename: str) -> bool:
        if filename in odds_completed:
            return True
        # O 始まりでないファイルは RA/SE/HR 系なので RACE_completed にあればスキップ
        if not filename.startswith("O") and filename in race_completed:
            return True
        return False

    opt_label = ("セットアップ/全再ダウンロード" if option in (3, 4)
                 else "通常/ローカルキャッシュ優先")
    logger.info(f"JVOpen RACE from={from_time} option={option} ({opt_label})...")

    with BlockingCallGuard(f"JVOpen(RACE, option={option})", JVOPEN_TIMEOUT_SEC, logger):
        result = jv.JVOpen(DATASPEC_RACE, from_time, option, 0, 0, "")

    rc = result[0] if isinstance(result, tuple) else result
    logger.info(f"JVOpen rc={rc}")
    if rc < 0:
        logger.error(f"JVOpen エラー: rc={rc}")
        return

    file_records: list[dict] = []
    current_file = ""
    skip_current = False
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
            if current_file and not skip_current:
                on_file_done(current_file, file_records)
            new_file = r[3] if len(r) > 3 else (r[2] if len(r) > 2 else "")
            if new_file and hasattr(new_file, "strip"):
                new_file = new_file.strip()
            current_file = new_file
            file_records = []
            wait_count = 0
            skip_current = skip_fn(current_file)
            if skip_current:
                # JVSkip の戻り値は VT_VOID（4.9 仕様書・5.0.0 の型情報とも「戻り値なし」）。
                # pywin32 は None を返すため、以前の `if rc_skip == 0:` は必ず False になり、
                # 4 箇所すべてが恒常的に「JVSkip 失敗 → 読み捨てモード」に落ちていた。
                # 戻り値は見ず、無条件に成功として扱う（2026-08-23 修正）。
                jv.JVSkip()
                skip_count += 1
                current_file = ""
                skip_current = False
            continue
        elif rc2 == -3:  # ダウンロード中
            wait_count += 1
            if wait_count % 10 == 0:
                logger.info(f"ダウンロード待機中... ({wait_count * 10}秒)")
            time.sleep(1)
            continue
        elif rc2 < -1:
            logger.error(f"JVRead エラー: rc={rc2}, ファイル={current_file}")
            break

        if skip_current:
            continue

        read_count += 1
        buf_data = r[1] if r[1] else ""
        rec_id = buf_data[:2] if len(buf_data) >= 2 else ""
        # O レコードだけメモリに積む（O6 は 83KB/件あるので全部持つと重い）
        if rec_id in ODDS_REC_IDS:
            file_records.append({"rec_id": rec_id, "data": buf_data})

    try:
        jv.JVClose()
    except Exception:
        pass

    breakdown = " / ".join(f"{k}:{v}" for k, v in sorted(by_rec.items())) or "なし"
    logger.info(
        f"=== 確定オッズ バックフィル完了: {total['files']} ファイル / "
        f"{total['posted']}/{total['records']} 件 POST / {total['skipped']} スキップ ==="
    )
    logger.info(f"    レコード種別の内訳: {breakdown}")


def main() -> None:
    ap = argparse.ArgumentParser(description="確定オッズ O1〜O6 バックフィル")
    ap.add_argument("--from-year", type=int, default=2024, help="取得開始年 (default: 2024)")
    ap.add_argument(
        "--option",
        type=int,
        default=1,
        choices=[1, 3, 4],
        help="JVOpen option: 1=通常(キャッシュ・保持窓は1年) / "
             "3=セットアップ(ダイアログ有) / 4=セットアップ(ダイアログ無) (default: 1)",
    )
    ap.add_argument(
        "--backend-url",
        default=None,
        help="POST 先のバックエンド URL（既定は .env の BACKEND_URL）",
    )
    args = ap.parse_args()

    global BACKEND_URL
    if args.backend_url:
        BACKEND_URL = args.backend_url
    logger.info(
        f"確定オッズ バックフィル開始: from_year={args.from_year}, "
        f"option={args.option}, backend={BACKEND_URL}"
    )
    if not API_KEY:
        logger.warning("API_KEY が空。認証が有効なバックエンドでは 401 になる。")

    try:
        import win32com.client

        jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
        rc = jv.JVInit("UNKNOWN")
        if rc != 0:
            logger.error(f"JVInit エラー: rc={rc}")
            sys.exit(1)

        logger.info("JVLink 初期化 OK")
        run_odds_backfill(jv, from_year=args.from_year, option=args.option)

    except ImportError:
        logger.error("win32com.client が見つかりません。Windows Python 環境で実行してください。")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"予期しないエラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
