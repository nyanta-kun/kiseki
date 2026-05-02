"""TOKU TK record Phase 4: py600-1200 を走査してblood_reg #1を見つける。

実行方法（RunAdhoc 経由）:
  adhoc_cmd.txt に "probe_toku.py" を書いて kiseki-RunAdhoc を実行する。
"""
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


def is_blood_reg(data: str, py1: int) -> bool:
    """Python position (1-indexed) から 10 chars が blood_reg 候補かチェック。
    条件: 10桁ASCII数字 かつ 直前の char が数字でない。
    """
    idx = py1 - 1
    if idx + 10 > len(data):
        return False
    if not all(0x30 <= ord(data[idx + i]) <= 0x39 for i in range(10)):
        return False
    if idx > 0 and 0x30 <= ord(data[idx - 1]) <= 0x39:
        return False  # preceded by digit
    return True


def show_block(data: str, py_start: int, length: int) -> None:
    """py_start (1-indexed) から length 文字をコンパクト表示。"""
    line_buf = []
    for i in range(length):
        p = py_start + i
        if p > len(data):
            break
        b = ord(data[p - 1])
        if b < 0x80 and b >= 0x20:
            line_buf.append(chr(b))
        elif b < 0x80:
            line_buf.append('_')
        else:
            line_buf.append('J')  # Japanese char
    # 60文字ずつ改行
    s = "".join(line_buf)
    for j in range(0, len(s), 80):
        log.info(f"  py{py_start+j:5d}: {s[j:j+80]!r}")


def analyze(data: str, idx: int) -> None:
    log.info(f"{'='*60}")
    log.info(f"TK #{idx} len={len(data)} race={data[19:27]!r}")

    # py600-1200 をコンパクト表示
    log.info("  --- py600-1200 compact (. = Japanese char) ---")
    show_block(data, 600, 600)

    # blood_reg を 1-indexed 位置で全探索
    log.info("  --- blood_reg 候補探索 (py 509-21000, pre-non-digit) ---")
    found = []
    for p in range(509, min(len(data) - 9, 5000)):
        if is_blood_reg(data, p):
            found.append(p)
    log.info(f"  blood_reg positions: {found[:40]}")
    if len(found) >= 2:
        gaps = [found[i+1] - found[i] for i in range(min(len(found)-1, 40))]
        log.info(f"  gaps: {gaps[:40]}")

    # 最初の 5 頭分の blood_reg と horse_name を表示 (offset 50 から 18 chars)
    log.info("  --- first 5 horses ---")
    for i, p in enumerate(found[:5]):
        blood_reg = data[p-1:p+9]
        name_raw = data[p+49:p+67]  # horse_name at +50, 18 chars
        name_stripped = name_raw.strip()
        # 調教師名候補: +68 から
        trainer_raw = data[p+67:p+85]
        trainer_stripped = trainer_raw.strip()
        log.info(
            f"  horse {i}: py{p} blood_reg={blood_reg!r} "
            f"name={name_stripped!r} trainer?={trainer_stripped!r}"
        )

    # tail
    tail = data[-50:]
    log.info(f"  tail[-50:] ords: {[ord(c) for c in tail]}")


def main() -> None:
    if not JRAVAN_SID:
        sys.exit(1)

    jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
    rc = jv.JVInit(JRAVAN_SID)
    if rc != 0:
        log.error(f"JVInit rc={rc}")
        sys.exit(1)
    log.info(f"JVInit OK rc={rc}")

    from_time = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d") + "000000"
    result = jv.JVOpen("TOKU", from_time, 1, 0, 0, "")
    rc = result[0] if isinstance(result, tuple) else result
    n_files = result[1] if isinstance(result, tuple) else "?"
    log.info(f"JVOpen rc={rc} files={n_files}")
    if rc < 0:
        jv.JVClose()
        return

    tk_count = 0
    while True:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]
        if ret_code == 0:
            break
        if ret_code < -3:
            break
        if ret_code in (-1, -3):
            continue
        data = r[1] if len(r) > 1 else ""
        if not data or data[:2] != "TK":
            continue
        tk_count += 1
        if tk_count <= 1:
            analyze(data, tk_count)
        if tk_count >= 1:
            break

    jv.JVClose()
    log.info(f"完了: TK {tk_count} 件")


if __name__ == "__main__":
    main()
