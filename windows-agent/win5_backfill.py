"""WIN5（WF レコード）バックフィルスクリプト。

WIN5 は蓄積系 dataspec **`RACE`** に含まれる（`docs/jvdata-spec.md` 30項）。
セットアップ（option=3）なら **2011年4月以降の全 WIN5** が取れる。

## 🔴 なぜ独立したスクリプトが必要か

`jvlink_historical.py` の取込は `COMPLETED_KEY_RACE = "RACE"` を
`jvlink_agent.py` と**共有**している。既に処理済みの過去ファイルは
`skip_fn` で **JVSkip され中身が読まれない**。

したがって `jvlink_historical.py` のレコードフィルタに `"WF"` を足しただけでは
**過去分の WF は1件も取れない**。「コードは入れたのに何も増えない」という
0B11（速報馬体重）と同じ型の失敗になる。

`payout_backfill.py` が HR で同じ問題を解いており、本スクリプトはその写しである。
**独立した `WIN5_completed.txt`** を持つ。

## 🔴 パースはサーバ側で行う（実機のパーサに依存しない）

`windows-agent/jvlink_parser.py` は **git 管理外**で、実機のものは 2026-05-04 付と
4か月古い。更新手順も自動化も無い。さらに main のパーサは
`from ..bet_types import BET_TYPES` という**相対 import** を持つため、そのまま
実機へ置くと単体 import できず、**既存の HR 払戻経路まで巻き込んで壊れる**
（2026-09-02 に実機で確認）。

そこで本スクリプトは **WF の生レコードをそのまま POST** し、
`/api/import/win5` がサーバ側で `parse_wf` する。`/api/import/weights`（0B11）が
同じ理由で採っている形と揃えた。**実機のパーサを更新する必要はない。**

## 🔴 WF がどのファイル名で届くかは未確認

`payout_backfill.py` は `filename.startswith("H")` で HR ファイルを見分けているが、
**WF の接頭辞は分かっていない**。推測でスキップ規則を書くと、当たっていなければ
全件取りこぼし、外れていれば全ファイルを読むだけで、どちらも無言で終わる。

そこで既定では **`WIN5_completed.txt` に無いファイルはすべて読む**（遅いが確実）。
`--discover` を付けると POST せずにファイル名別の rec_id 内訳だけを出すので、
1回流せば接頭辞が実測で分かる。分かったら `--only-prefix X` で以後を高速化する。

## 使い方

    # 1回目: どのファイル名に WF が入るかを調べる（DB は触らない）
    python win5_backfill.py --from-year 2011 --option 3 --discover

    # 2回目: 実際に取り込む（接頭辞が分かっていれば --only-prefix で高速化）
    python win5_backfill.py --from-year 2011 --option 3

⚠️ option=3（セットアップ）は JVOpen が数時間ブロックする。
   メンテナンス窓（既定: 毎週火 08:00-15:00）を避けること。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("win5_backfill.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
COMPLETED_DIR = DATA_DIR / "completed"
COMPLETED_DIR.mkdir(parents=True, exist_ok=True)

# 🔴 RACE_completed.txt とは独立させる（共有すると JVSkip されて何も取れない）
WIN5_COMPLETED_FILE = COMPLETED_DIR / "WIN5_completed.txt"

DATASPEC_RACE = "RACE"
POST_BATCH_SIZE = 50

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BASE_DIR.parent / ".env")
except ImportError:
    pass

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_KEY = os.getenv("AGENT_API_KEY", "")


def load_completed() -> set[str]:
    if not WIN5_COMPLETED_FILE.exists():
        return set()
    return set(WIN5_COMPLETED_FILE.read_text(encoding="utf-8").splitlines())


def mark_completed(filename: str, completed: set[str]) -> None:
    if filename in completed:
        return
    with WIN5_COMPLETED_FILE.open("a", encoding="utf-8") as f:
        f.write(filename + "\n")
    completed.add(filename)


def post_to_backend(endpoint: str, payload: dict, timeout: int = 300) -> dict | None:
    """POST して**レスポンス本文を返す**。

    🔴 200 が返ったことを成功と見なさない。`unresolved_races` を読むために
    本文を返す（0B11 は 200 を返し続けながら全件捨てていた）。
    """
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
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        logger.error("POST %s HTTP %s: %s", endpoint, e.code, e.read()[:200])
        return None
    except Exception as e:
        logger.error("POST %s 失敗: %s", endpoint, e)
        return None


def run_win5_backfill(
    jv,
    *,
    from_year: int = 2011,
    option: int = 1,
    discover: bool = False,
    only_prefix: str | None = None,
) -> None:
    from_time = f"{from_year}0101000000"

    mode = "調査（POST しない）" if discover else "取込"
    logger.info(
        "=== WIN5 バックフィル開始: %s年以降 / option=%s / %s ===", from_year, option, mode
    )

    completed = load_completed()
    logger.info("[WIN5_completed] 処理済み: %d 件", len(completed))
    if only_prefix:
        logger.info("接頭辞 %r 以外のファイルは JVSkip します", only_prefix)
    else:
        logger.info(
            "接頭辞の指定なし → 未処理ファイルはすべて読みます"
            "（遅いが確実。--discover で接頭辞を実測してから --only-prefix を付けると速い）"
        )

    total = {"files": 0, "events": 0, "unresolved": 0, "skipped": 0}
    discovered: dict[str, Counter] = defaultdict(Counter)

    def skip_fn(filename: str) -> bool:
        if filename in completed:
            return True
        # 接頭辞が実測で分かっている場合だけ絞る。既定では絞らない
        if only_prefix and not filename.startswith(only_prefix):
            return True
        return False

    def on_file_done(filename: str, file_records: list[dict]) -> None:
        if discover:
            for r in file_records:
                discovered[filename[:1]][r.get("rec_id", "??")] += 1
            wf_n = sum(1 for r in file_records if r.get("rec_id") == "WF")
            if wf_n:
                logger.info("  [%s] WF %d 件", filename, wf_n)
            return

        wf = [r for r in file_records if r.get("rec_id") == "WF"]
        if not wf:
            mark_completed(filename, completed)
            return

        # パースはサーバ側。生レコードをそのまま送る（実機のパーサに依存しない）
        records = [{"rec_id": r.get("rec_id", ""), "data": r.get("data", "")} for r in wf]

        for i in range(0, len(records), POST_BATCH_SIZE):
            batch = records[i : i + POST_BATCH_SIZE]
            res = post_to_backend("/api/import/win5", {"records": batch})
            if res is None:
                logger.error("  [%s] POST 失敗。completed に印を付けずに次へ", filename)
                return
            total["events"] += res.get("imported", 0)
            if res.get("unparsed"):
                logger.warning("  [%s] サーバ側で %d 件をパースできませんでした",
                               filename, res["unparsed"])
            unresolved = res.get("unresolved_races", 0)
            total["unresolved"] += unresolved
            if unresolved:
                # 🔴 200 でも対象レースが解決できていないなら取り込めていないのと同じ
                logger.warning(
                    "  [%s] 対象レースを %d 脚ぶん解決できませんでした"
                    "（races に該当 jravan_race_id が無い）", filename, unresolved,
                )
        logger.info("  [%s] WIN5 %d 件を取込", filename, len(records))
        total["files"] += 1
        mark_completed(filename, completed)

    opt_label = "セットアップ/全再ダウンロード" if option == 3 else "通常/ローカルキャッシュ優先"
    logger.info("JVOpen %s from=%s option=%s (%s)...", DATASPEC_RACE, from_time, option, opt_label)

    _done = threading.Event()

    def _heartbeat() -> None:
        start = time.time()
        while not _done.is_set():
            _done.wait(timeout=30)
            if not _done.is_set():
                logger.info("JVOpen 待機中... %d秒経過", int(time.time() - start))

    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()
    try:
        result = jv.JVOpen(DATASPEC_RACE, from_time, option, 0, 0, "")
    finally:
        _done.set()

    rc = result[0] if isinstance(result, tuple) else result
    logger.info("JVOpen rc=%s", rc)
    if rc != 0:
        logger.error("JVOpen エラー: rc=%s", rc)
        return

    file_records: list[dict] = []
    current_file = ""
    skip_current = False
    read_count = skip_count = wait_count = 0

    while True:
        r = jv.JVRead("", 256000, "")
        rc2 = r[0]

        if rc2 == 0:  # EOF
            if current_file and not skip_current:
                on_file_done(current_file, file_records)
            logger.info("JVRead 完了: 読込=%d スキップファイル=%d", read_count, skip_count)
            break
        if rc2 == -1:  # ファイル切り替わり
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
                # JVSkip の戻り値は VT_VOID。pywin32 は None を返すので戻り値を見ない
                # （payout_backfill.py の 2026-08-23 修正と同じ理由）
                jv.JVSkip()
                skip_count += 1
                total["skipped"] += 1
                current_file = ""
                skip_current = False
            continue
        if rc2 == -3:  # ダウンロード中
            wait_count += 1
            if wait_count % 10 == 0:
                logger.info("ダウンロード待機中... (%d秒)", wait_count * 10)
            time.sleep(1)
            continue
        if rc2 < -1:
            logger.error("JVRead エラー: rc=%s, ファイル=%s", rc2, current_file)
            break

        if skip_current:
            continue
        read_count += 1
        buf_data = r[1] if r[1] else ""
        rec_id = buf_data[:2] if len(buf_data) >= 2 else ""
        file_records.append({"rec_id": rec_id, "data": buf_data})

    try:
        jv.JVClose()
    except Exception:
        pass

    if discover:
        logger.info("=== 調査結果: ファイル名の先頭1文字ごとの rec_id 内訳 ===")
        for prefix in sorted(discovered):
            counts = discovered[prefix].most_common(8)
            wf_n = discovered[prefix].get("WF", 0)
            mark = "  ← WF はここ" if wf_n else ""
            logger.info("  '%s': %s%s", prefix, counts, mark)
        logger.info(
            "WF が出た接頭辞が分かったら、次回から --only-prefix <文字> を付けると速くなります"
        )
        return

    logger.info(
        "=== WIN5 バックフィル完了: %d ファイル / %d 開催 / 未解決脚 %d / スキップ %d ===",
        total["files"], total["events"], total["unresolved"], total["skipped"],
    )
    # 🔴 0B11 の教訓: 200 が返ったことは取り込めた証拠にならない
    if total["events"] == 0:
        logger.error(
            "🔴 1件も取り込めていません。completed の共有・接頭辞の絞りすぎ・"
            "WF が届いていないことのいずれかを疑ってください"
        )
    if total["unresolved"]:
        logger.warning(
            "🔴 対象レースを %d 脚ぶん解決できませんでした。"
            "races の取込が先に済んでいるか確認してください", total["unresolved"],
        )


def main() -> None:
    p = argparse.ArgumentParser(description="WIN5（WF レコード）バックフィル")
    p.add_argument("--from-year", type=int, default=2011,
                   help="取得開始年（WIN5 は 2011年4月開始・default: 2011）")
    p.add_argument("--option", type=int, default=1, choices=[1, 3],
                   help="JVOpen option: 1=通常(キャッシュ) 3=セットアップ(全再DL)")
    p.add_argument("--discover", action="store_true",
                   help="POST せず、ファイル名別の rec_id 内訳だけを出す（接頭辞の実測用）")
    p.add_argument("--only-prefix",
                   help="このファイル名接頭辞だけ読む（--discover で実測してから指定する）")
    args = p.parse_args()

    try:
        import win32com.client

        jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
        rc = jv.JVInit("UNKNOWN")
        if rc != 0:
            logger.error("JVInit エラー: rc=%s", rc)
            sys.exit(1)
        logger.info("JVLink 初期化 OK")
        run_win5_backfill(
            jv,
            from_year=args.from_year,
            option=args.option,
            discover=args.discover,
            only_prefix=args.only_prefix,
        )
    except ImportError:
        logger.error("win32com.client が見つかりません。Windows Python 環境で実行してください。")
        sys.exit(1)
    except Exception as e:
        logger.exception("予期しないエラー: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
