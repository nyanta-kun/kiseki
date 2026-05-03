"""TOKU TK レコードを生 dump する診断スクリプト。

JVRead が返す全 TK レコードを以下の形で保存する:
  - 1 レコード 1 行の JSON (length, race_key, raw_bytes_hex)
  - SJIS 解釈と Unicode 解釈の両方を試す

実行: adhoc_cmd.txt に "probe_toku.py" を書いて kiseki-RunAdhoc を実行。
出力: C:\\kiseki\\windows-agent\\probe_toku.jsonl
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import win32com.client
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

log_path = Path(__file__).resolve().parent / "probe_toku.log"
out_path = Path(__file__).resolve().parent / "probe_toku.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(log_path), encoding="utf-8", mode="w"),
    ],
)
log = logging.getLogger("probe_toku")

JRAVAN_SID = os.getenv("JRAVAN_SID", "")


def main() -> None:
    if not JRAVAN_SID:
        log.error("JRAVAN_SID empty")
        sys.exit(1)

    jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
    rc = jv.JVInit(JRAVAN_SID)
    log.info(f"JVInit rc={rc}")
    if rc != 0:
        sys.exit(1)

    from_time = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d") + "000000"
    result = jv.JVOpen("TOKU", from_time, 1, 0, 0, "")
    rc = result[0] if isinstance(result, tuple) else result
    n_files = result[1] if isinstance(result, tuple) else "?"
    log.info(f"JVOpen rc={rc} files={n_files}")
    if rc < 0:
        jv.JVClose()
        return

    tk_count = 0
    other_count = 0
    rec_id_counts: dict[str, int] = {}

    with open(out_path, "w", encoding="utf-8") as fout:
        while True:
            try:
                r = jv.JVRead("", 256000, "")
            except Exception as e:
                log.error(f"JVRead exception: {e}")
                break
            ret_code = r[0]
            if ret_code == 0:
                log.info("JVRead returned 0 (EOF)")
                break
            if ret_code < -3:
                log.error(f"JVRead error ret_code={ret_code}")
                break
            if ret_code in (-1, -3):
                continue

            data = r[1] if len(r) > 1 else ""
            if not data:
                continue

            rec_id = data[:2]
            rec_id_counts[rec_id] = rec_id_counts.get(rec_id, 0) + 1

            if rec_id != "TK":
                other_count += 1
                continue

            tk_count += 1

            # raw bytes (Latin-1 と仮定して bytes に戻す → これが SJIS の真のバイト列のはず)
            try:
                latin1_bytes = data.encode("latin-1")
                latin1_hex = latin1_bytes.hex()
            except Exception:
                latin1_hex = None

            # Unicode (Python str) の各 char の ord
            ords = [ord(c) for c in data]

            # 各バイト解釈で SJIS デコード
            sjis_decoded = None
            try:
                sjis_decoded = data.encode("latin-1").decode("cp932", errors="replace")
            except Exception:
                pass

            entry = {
                "tk_index": tk_count,
                "char_len": len(data),
                "byte_len": len(latin1_bytes) if latin1_hex else None,
                "head_chars": data[:30],
                "head_ords": ords[:30],
                "race_key_chars": data[19:27] if len(data) > 27 else None,
                "latin1_hex": latin1_hex,
                "sjis_decoded": sjis_decoded,
                "all_ords": ords,
            }
            fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fout.flush()

            # 最初の 3 件は詳細ログ
            if tk_count <= 3:
                log.info(f"=== TK #{tk_count} ===")
                log.info(f"  char_len={len(data)} byte_len={entry['byte_len']}")
                log.info(f"  head: {data[:30]!r}")
                log.info(f"  sjis_first_200: {(sjis_decoded[:200] if sjis_decoded else None)!r}")

            if tk_count >= 30:
                break

    jv.JVClose()
    log.info(f"完了: TK {tk_count} 件 / その他 {other_count} 件")
    log.info(f"レコード種別カウント: {rec_id_counts}")
    log.info(f"出力: {out_path}")


if __name__ == "__main__":
    main()
