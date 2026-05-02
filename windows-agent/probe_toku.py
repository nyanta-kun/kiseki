"""TOKU DataSpec の TK レコード・バイト構造を確認する診断スクリプト。

実行方法（RunAdhoc 経由）:
  adhoc_cmd.txt に "probe_toku.py" を書いて kiseki-RunAdhoc を実行する。
  出力は jvlink_agent.log ではなく probe_toku.log に書き出す。
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("probe_toku.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("probe_toku")

JRAVAN_SID = os.getenv("JRAVAN_SID", "")

def _decode(raw: str, start: int, end: int) -> str:
    """SJIS テキストフィールドを UTF-8 文字列に変換（1-indexed, inclusive）。"""
    try:
        return raw[start - 1 : end].encode("latin-1").decode("cp932").strip()
    except Exception:
        return raw[start - 1 : end].strip()

def _s(raw: str, start: int, end: int) -> str:
    return raw[start - 1 : end].strip()

def analyze_tk_record(data: str, idx: int) -> None:
    """TK レコード 1 件のバイト構造を詳細ログ出力する。"""
    log.info(f"\n{'='*60}")
    log.info(f"TK record #{idx}  total_len={len(data)}")

    # 共通ヘッダー (1-27)
    log.info(f"  pos  1- 2 rec_id      = {_s(data,1,2)!r}")
    log.info(f"  pos  3    data_type   = {_s(data,3,3)!r}")
    log.info(f"  pos  4-11 created     = {_s(data,4,11)!r}")
    log.info(f"  pos 12-15 year        = {_s(data,12,15)!r}")
    log.info(f"  pos 16-19 month_day   = {_s(data,16,19)!r}")
    log.info(f"  pos 20-21 course      = {_s(data,20,21)!r}")
    log.info(f"  pos 22-23 kai         = {_s(data,22,23)!r}")
    log.info(f"  pos 24-25 day         = {_s(data,24,25)!r}")
    log.info(f"  pos 26-27 race_num    = {_s(data,26,27)!r}")

    # レース情報フィールドを探索（28 から）
    log.info(f"  pos 28-71  field_28_71  = {_decode(data,28,71)!r}")
    log.info(f"  pos 72-91  field_72_91  = {_decode(data,72,91)!r}")
    log.info(f"  pos 92-111 field_92_111 = {_decode(data,92,111)!r}")
    log.info(f"  pos112-115 field112_115 = {_s(data,112,115)!r}")
    log.info(f"  pos116     field116     = {_s(data,116,116)!r}")
    log.info(f"  pos117     field117     = {_s(data,117,117)!r}")
    log.info(f"  pos118-121 field118_121 = {_s(data,118,121)!r}")
    log.info(f"  pos122     field122     = {_s(data,122,122)!r}")
    log.info(f"  pos123     field123     = {_s(data,123,123)!r}")
    log.info(f"  pos124-125 field124_125 = {_s(data,124,125)!r}  ← 登録頭数?")
    log.info(f"  pos126-127 field126_127 = {_s(data,126,127)!r}")

    # 128 以降: 馬エントリーを試す (63バイト固定想定)
    HORSE_SIZE_CANDIDATES = [62, 63, 64]
    for horse_size in HORSE_SIZE_CANDIDATES:
        n_horses = int(_s(data, 124, 125) or "0") if _s(data, 124, 125).isdigit() else 0
        if n_horses == 0:
            break
        log.info(f"\n  --- horse_size={horse_size} candidate (n_horses={n_horses}) ---")
        for i in range(min(n_horses, 3)):
            base = 127 + i * horse_size  # 0-indexed → 1-indexed offset
            if base + horse_size > len(data):
                log.info(f"    horse {i}: out of bounds (base={base})")
                break
            entry = data[base : base + horse_size]
            try:
                blood_reg = _s(entry, 1, 10)
                name = entry[10:46].encode("latin-1").decode("cp932").strip()
                sex_code = entry[46:47].strip()
                age = entry[47:49].strip()
                east_west = entry[49:50].strip()
                trainer_code = entry[50:55].strip()
                trainer_name = entry[55:63].encode("latin-1").decode("cp932").strip() if horse_size >= 63 else ""
                log.info(
                    f"    horse {i}: blood={blood_reg!r} name={name!r} "
                    f"sex={sex_code!r} age={age!r} ew={east_west!r} "
                    f"t_code={trainer_code!r} t_name={trainer_name!r}"
                )
            except Exception as e:
                log.info(f"    horse {i}: parse error {e}")
        break  # 1候補のみ試す

    # 生バイト（先頭160バイト）
    log.info(f"\n  raw latin-1 (first 200):")
    raw_bytes = data[:200].encode("latin-1") if len(data) >= 200 else data.encode("latin-1")
    # 10バイトずつ区切って表示
    for start in range(0, len(raw_bytes), 10):
        chunk = raw_bytes[start:start+10]
        hex_str = " ".join(f"{b:02x}" for b in chunk)
        try:
            txt = chunk.decode("cp932", errors="replace")
        except Exception:
            txt = "?"
        log.info(f"    [{start+1:3d}-{start+len(chunk):3d}] {hex_str:<29} | {txt!r}")


def main() -> None:
    if not JRAVAN_SID:
        log.error("JRAVAN_SID 未設定")
        sys.exit(1)

    jv = win32com.client.Dispatch("JVDTLab.JVLink.1")
    rc = jv.JVInit(JRAVAN_SID)
    if rc != 0:
        log.error(f"JVInit rc={rc}")
        sys.exit(1)
    log.info(f"JVInit OK rc={rc}")

    # 2週間前からの TOKU データを差分取得 (option=1)
    from_time = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d") + "000000"
    log.info(f"JVOpen TOKU from={from_time} option=1")

    result = jv.JVOpen("TOKU", from_time, 1, 0, 0, "")
    if isinstance(result, tuple):
        rc, n_files = result[0], result[1]
    else:
        rc, n_files = result, "?"
    log.info(f"JVOpen rc={rc} files={n_files}")

    if rc < 0:
        log.error(f"JVOpen error rc={rc}")
        jv.JVClose()
        return

    tk_count = 0
    while True:
        r = jv.JVRead("", 256000, "")
        ret_code = r[0]
        if ret_code == 0:
            break
        if ret_code < -3:
            log.warning(f"JVRead error rc={ret_code}")
            break
        if ret_code in (-1, -3):
            continue
        data = r[1] if len(r) > 1 else ""
        if not data:
            continue
        rec_id = data[:2]
        if rec_id != "TK":
            continue
        tk_count += 1
        if tk_count <= 5:
            analyze_tk_record(data, tk_count)
        if tk_count >= 5:
            log.info("5件分析完了 → 終了")
            break

    if tk_count == 0:
        log.warning("TK レコードが見つかりませんでした。TOKU DataSpec に TK レコードがない可能性があります。")

    jv.JVClose()
    log.info(f"完了: TK {tk_count} 件分析")


if __name__ == "__main__":
    main()
