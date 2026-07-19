"""Protocol-based 1403 DM 一括取得パイプライン (Windows側).

機能:
  1. JV-Next 動作確認 (停止時は起動)
  2. session KEY 検証 (probe fetch、失効時は pktmon で再抽出)
  3. 指定 (date, course) リストの全 12 レース 1403 取得
  4. 永続ストア (C:\\kiseki\\data\\dm_1403) に保存 (zlib 圧縮)
  5. importer (jvnext_dm_importer.py) を起動して DB 反映

使用例:
  python protocol_dm_pipeline.py --date 20260426 --courses 03,05,08
  python protocol_dm_pipeline.py --dates 20260426,20260419 --courses all
  python protocol_dm_pipeline.py --session-file session.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import zlib
from pathlib import Path

# プロトコル送信器をインポート (同フォルダ)
sys.path.insert(0, r"C:\kiseki\windows-agent")
from protocol_fetch import (build_request, send_request, parse_chunked_response,
                            decompress_payload)


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
PYTHON_EXE = r"C:\Python312-32\python.exe"
JVNEXT_EXE = r"C:\Program Files (x86)\JRA-VAN\NEXT5\JVNextCore.exe"
PKTMON_EXE = r"C:\Windows\Sysnative\pktmon.exe"
if not Path(PKTMON_EXE).exists():
    PKTMON_EXE = r"C:\Windows\System32\pktmon.exe"

KEY_FILE = Path(r"C:\kiseki\data\session_key.txt")
KEY_META_FILE = Path(r"C:\kiseki\data\session_key.meta")
PERSISTENT_STORE = Path(r"C:\kiseki\data\dm_1403")
LIVE_CACHE = Path(r"C:\Users\ysuzuki\AppData\Local\JRA-VAN\NEXT\cache\1403")
PKTMON_ETL = Path(r"C:\kiseki\data\pktmon_pipeline.etl")
PKTMON_PCAP = Path(r"C:\kiseki\data\pktmon_pipeline.pcap")
LOG_FILE = Path(r"C:\kiseki\data\protocol_pipeline.log")

# 1403 DATA encoding
DATA_PREFIX = "05900403"  # 1403 (DM) 用 prefix

# pktmon filter IPs (next5.jra-van.jp / app / jra-van)
# 注意: JRA-VAN NEXT5 サーバーの実IPはCDN/ロードバランサ経由で変動する
# (2026-04-29確認時は211.6.76.x、2026-07-04確認では148.109.52.x に変わっていた)。
# IP固定フィルタだと無音で全滅するため、ポート80のみでフィルタする。
JRAVAN_IPS: list[str] = []

# プローブ対象 (KEY 有効性確認)
PROBE_DATA = "0500030320260502"  # 開催情報リスト (FLG=1, 84b 程度返る)

KEY_PATTERN = re.compile(rb"05HA1101[0-9A-F]+[\r\n]+([0-9A-F]{50})")


# ---------------------------------------------------------------------------
# ログ
# ---------------------------------------------------------------------------
def log(msg: str = ""):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# JV-Next プロセス管理
# ---------------------------------------------------------------------------
def is_jvnext_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq JVNextCore.exe"],
            capture_output=True, text=True, timeout=10,
        )
        return "JVNextCore.exe" in out.stdout
    except Exception:
        return False


def start_jvnext() -> bool:
    """JVNextCore を起動する (デスクトップセッション必須)."""
    log("[jv] starting JVNextCore...")
    try:
        # WScript.Shell 経由で 1=NormalActiveWindow で起動
        ps_cmd = (
            "$wshell = New-Object -ComObject WScript.Shell; "
            f"$wshell.Run('\"{JVNEXT_EXE}\"', 1, $false) | Out-Null"
        )
        subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True, timeout=15)
        # 起動確認 (最大30秒待機)
        for _ in range(30):
            time.sleep(1)
            if is_jvnext_running():
                log("[jv] JVNextCore confirmed running")
                return True
        log("[jv] timeout waiting for JVNextCore")
        return False
    except Exception as e:
        log(f"[jv] start failed: {e}")
        return False


def stop_jvnext():
    log("[jv] stopping JVNextCore...")
    subprocess.run(["taskkill", "/F", "/IM", "JVNextCore.exe"],
                   capture_output=True, timeout=15)
    time.sleep(3)


# ---------------------------------------------------------------------------
# pktmon 制御
# ---------------------------------------------------------------------------
def pktmon_setup_filters():
    """pktmon フィルタを設定. JRA-VAN サーバーIPはCDN経由で変動するため
    IP固定でなくポート80のみでフィルタする (JRAVAN_IPS指定時はIP絞り込みも追加)."""
    subprocess.run([PKTMON_EXE, "filter", "remove"], capture_output=True, timeout=10)
    if JRAVAN_IPS:
        for i, ip in enumerate(JRAVAN_IPS):
            subprocess.run(
                [PKTMON_EXE, "filter", "add", f"JRAVAN{i+1}", "-i", ip, "-p", "80"],
                capture_output=True, timeout=10,
            )
    else:
        subprocess.run(
            [PKTMON_EXE, "filter", "add", "JRAVANPORT80", "-p", "80"],
            capture_output=True, timeout=10,
        )


def pktmon_start():
    PKTMON_ETL.unlink(missing_ok=True)
    subprocess.run(
        [PKTMON_EXE, "start", "--capture", "--pkt-size", "0",
         "--file-name", str(PKTMON_ETL)],
        capture_output=True, timeout=15,
    )


def _extract_key_from_reassembled_streams(pcap_path: Path) -> str | None:
    """PCAP を TCP ストリーム単位で seq 順に再組み立てしてから KEY を検索する.

    auth 応答は複数 TCP セグメントに分割されるため、生バイト連結
    (パケット間に pcap レコードヘッダ等が挟まる) では正規表現が
    セグメント境界をまたぐ KEY 文字列を検出できない。
    """
    try:
        from scapy.all import rdpcap, TCP, IP, Raw
    except ImportError:
        return None
    try:
        pkts = rdpcap(str(pcap_path))
    except Exception:
        return None
    from collections import defaultdict
    streams: dict[tuple[str, int], list[tuple[int, bytes]]] = defaultdict(list)
    for p in pkts:
        if not (p.haslayer(Raw) and p.haslayer(TCP) and p.haslayer(IP)):
            continue
        tcp = p[TCP]
        if tcp.sport != 80:
            continue
        streams[(p[IP].src, tcp.dport)].append((tcp.seq, bytes(p[Raw].load)))
    for segs in streams.values():
        segs.sort(key=lambda t: t[0])
        body = b"".join(s for _, s in segs)
        m = KEY_PATTERN.search(body)
        if m:
            return m.group(1).decode("ascii")
    return None


def pktmon_stop_and_extract_key() -> str | None:
    """pktmon 停止 → ETL を解析して KEY 抽出."""
    subprocess.run([PKTMON_EXE, "stop"], capture_output=True, timeout=15)
    if not PKTMON_ETL.exists():
        return None
    # 直接 ETL を生バイトスキャン (稀に境界をまたがず1パケットに収まるケースの高速パス)
    data = PKTMON_ETL.read_bytes()
    m = KEY_PATTERN.search(data)
    if m:
        return m.group(1).decode("ascii")
    # PCAP 変換して TCP ストリーム再組み立てで解析 (本命経路)
    PKTMON_PCAP.unlink(missing_ok=True)
    subprocess.run(
        [PKTMON_EXE, "etl2pcap", str(PKTMON_ETL), "-o", str(PKTMON_PCAP)],
        capture_output=True, timeout=60,
    )
    if not PKTMON_PCAP.exists():
        return None
    key = _extract_key_from_reassembled_streams(PKTMON_PCAP)
    if key:
        return key
    # 最終フォールバック: PCAP 生バイトスキャン
    data = PKTMON_PCAP.read_bytes()
    m = KEY_PATTERN.search(data)
    if m:
        return m.group(1).decode("ascii")
    return None


# ---------------------------------------------------------------------------
# KEY 管理
# ---------------------------------------------------------------------------
def load_saved_key() -> str | None:
    if not KEY_FILE.exists():
        return None
    return KEY_FILE.read_text(encoding="ascii").strip()


def save_key(key: str):
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key + "\n", encoding="ascii")
    KEY_META_FILE.write_text(str(time.time()), encoding="ascii")


def probe_key(key: str) -> bool:
    """指定 KEY で簡単な POST を投げて応答を見る. 200 + 非空応答 で valid."""
    try:
        req = build_request(key, 1, PROBE_DATA)
        raw = send_request(req, timeout=10.0)
        status, headers, payload = parse_chunked_response(raw)
        # 期待: 200 OK + payload が 80 バイト程度
        return "200 OK" in status and payload is not None and len(payload) >= 30
    except Exception as e:
        log(f"[key] probe failed: {e}")
        return False


def refresh_key() -> str | None:
    """JV-Next を kill→start して認証通信を pktmon で捕捉、新 KEY 抽出."""
    log("[key] refresh: kill JV-Next + capture auth")
    pktmon_setup_filters()
    stop_jvnext()
    pktmon_start()
    time.sleep(2)
    if not start_jvnext():
        pktmon_stop_and_extract_key()  # 念のため止める
        return None
    log("[key] waiting 30s for auth...")
    time.sleep(30)
    new_key = pktmon_stop_and_extract_key()
    if new_key:
        save_key(new_key)
        log(f"[key] new KEY: {new_key[:16]}...{new_key[-8:]}")
    else:
        log("[key] failed to extract new KEY")
    return new_key


def ensure_valid_key() -> str | None:
    """有効な KEY を返す (キャッシュ有効ならそれ、無効なら refresh)."""
    key = load_saved_key()
    if key and probe_key(key):
        log(f"[key] using cached KEY: {key[:16]}...{key[-8:]}")
        return key
    log("[key] cached KEY invalid or missing, refreshing")
    if not is_jvnext_running():
        log("[jv] JV-Next not running, starting first")
        start_jvnext()
        time.sleep(10)  # 認証完了待ち
        # JV-Next がもう動いている状態で再度 probe (起動済み KEY 抽出のため pktmon)
    return refresh_key()


# ---------------------------------------------------------------------------
# 1403 取得
# ---------------------------------------------------------------------------
def fetch_1403(key: str, date: str, course: str, race: int) -> bytes | None:
    """1 レース分の 1403 を取得. 解凍前のバイナリではなく解凍済バイトを返す."""
    cc = f"{int(course):02d}"
    nn = f"{race:02d}"
    data = f"{DATA_PREFIX}{date}{cc}{nn}"
    try:
        req = build_request(key, 0, data)
        raw = send_request(req, timeout=20.0)
        status, headers, payload = parse_chunked_response(raw)
        if not payload or len(payload) < 50:
            return None
        rsp_h, body = decompress_payload(payload)
        # 応答ヘッダ "0590 1403 0000 0621" = 成功. "...0010..." = データなし
        if "0010" in rsp_h[8:14]:
            return None
        return body
    except Exception as e:
        log(f"[fetch] err {date}-{course}-R{nn}: {e}")
        return None


def save_1403(date: str, course: str, race: int, body: bytes) -> Path:
    """解凍済データを zlib 圧縮して 1403{date}{CC}{NN}.dat に保存."""
    cc = f"{int(course):02d}"
    nn = f"{race:02d}"
    fname = f"1403{date}{cc}{nn}.dat"
    PERSISTENT_STORE.mkdir(parents=True, exist_ok=True)
    out = PERSISTENT_STORE / fname
    out.write_bytes(zlib.compress(body))
    return out


# ---------------------------------------------------------------------------
# importer 起動
# ---------------------------------------------------------------------------
def run_importer(dates: list[str]):
    """jvnext_dm_importer.py を呼び出して DB 反映."""
    importer = Path(r"C:\kiseki\windows-agent\jvnext_dm_importer.py")
    if not importer.exists():
        log("[importer] not found")
        return
    args = [PYTHON_EXE, str(importer), "--all"]
    log(f"[importer] running: {' '.join(args)}")
    proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        log(f"[importer] FAILED rc={proc.returncode}")
        log(f"  stdout: {proc.stdout[-500:]}")
        log(f"  stderr: {proc.stderr[-500:]}")
    else:
        # 出力末尾を要約として記録
        tail = "\n".join(proc.stdout.splitlines()[-10:])
        log(f"[importer] done: {tail}")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="単一日付 YYYYMMDD")
    ap.add_argument("--dates", help="複数日付 (カンマ区切り)")
    ap.add_argument("--courses", default="all",
                    help="場コード (カンマ区切り or 'all'). all=03,04,05,06,07,08,09,10")
    ap.add_argument("--session-file", help="JSON ファイル {dates:[],courses:[]}")
    ap.add_argument("--no-import", action="store_true", help="importer 起動をスキップ")
    ap.add_argument("--force-refresh-key", action="store_true",
                    help="キャッシュ KEY を無視して強制 refresh")
    ap.add_argument("--start-jv", action="store_true",
                    help="JV-Next 起動状態を確保 (停止中なら起動)")
    args = ap.parse_args()

    # 対象日付
    if args.session_file:
        with open(args.session_file) as f:
            session = json.load(f)
        dates = session.get("dates", [])
        courses_str = ",".join(session.get("courses", []))
    elif args.dates:
        dates = args.dates.split(",")
        courses_str = args.courses
    elif args.date:
        dates = [args.date]
        courses_str = args.courses
    else:
        ap.print_help()
        return 2

    if courses_str == "all":
        courses = ["03", "04", "05", "06", "07", "08", "09", "10"]
    else:
        courses = [c.strip().zfill(2) for c in courses_str.split(",") if c.strip()]

    log("=" * 70)
    log(f"protocol_dm_pipeline START")
    log(f"  dates: {dates}")
    log(f"  courses: {courses}")

    # JV-Next 確保
    if args.start_jv or not is_jvnext_running():
        if not is_jvnext_running():
            log("[jv] not running, starting...")
            start_jvnext()
            time.sleep(10)

    # KEY 確保
    if args.force_refresh_key:
        key = refresh_key()
    else:
        key = ensure_valid_key()
    if not key:
        log("[fatal] could not obtain valid KEY")
        return 1

    # 取得ループ
    total = 0
    saved = 0
    skipped = 0
    failed = 0
    for date in dates:
        for cc in courses:
            for race in range(1, 13):
                total += 1
                fname = f"1403{date}{cc}{race:02d}.dat"
                out = PERSISTENT_STORE / fname
                if out.exists() and out.stat().st_size > 0:
                    skipped += 1
                    continue
                body = fetch_1403(key, date, cc, race)
                if body:
                    save_1403(date, cc, race, body)
                    saved += 1
                else:
                    failed += 1
                time.sleep(0.2)
            log(f"  {date} CC={cc}: progress saved={saved} skipped={skipped} failed={failed}")

    log("=" * 70)
    log(f"fetch summary: total={total} saved={saved} skipped={skipped} failed={failed}")

    # importer 起動
    if not args.no_import and saved > 0:
        run_importer(dates)
    elif args.no_import:
        log("[importer] skipped (--no-import)")
    else:
        log("[importer] no new files, skipped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
