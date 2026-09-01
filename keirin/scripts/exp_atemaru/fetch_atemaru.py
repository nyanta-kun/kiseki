"""アテマル(yosoka_id=665)の公開予想を list API + detail ページで収集しアーカイブする。

作法: 生HTMLは必ずローカルへ保存してから解析する（keirin/src/scraper/netkeirin.py と同じ方針）。
"""
from __future__ import annotations
import json, re, sys, time
from pathlib import Path
import urllib.request, urllib.parse

BASE = "https://keirin.netkeiba.com"
YID = 665
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
ROOT = Path(__file__).resolve().parent / "atemaru"
LIST_DIR = ROOT / "list"; DET_DIR = ROOT / "detail"
LIST_DIR.mkdir(parents=True, exist_ok=True); DET_DIR.mkdir(parents=True, exist_ok=True)
INTERVAL = float(sys.argv[3]) if len(sys.argv) > 3 else 0.7


def _get(url: str, data: bytes | None = None) -> str:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def fetch_list(date: str) -> str:
    p = LIST_DIR / f"{date}.json"
    if p.exists():
        return p.read_text(encoding="utf-8")
    body = urllib.parse.urlencode({
        "input": "UTF-8", "output": "json", "show_id": "goods_list_main",
        "kaisai_date": date, "yosoka_id": YID, "jyo": "all"}).encode()
    s = _get(f"{BASE}/yoso/api/api_get_goods_list_prof.html", body)
    p.write_text(s, encoding="utf-8")
    time.sleep(INTERVAL)
    return s


def fetch_detail(gid: str) -> str:
    p = DET_DIR / f"{gid}.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    s = _get(f"{BASE}/yoso/detail/?id={gid}")
    p.write_text(s, encoding="utf-8")
    time.sleep(INTERVAL)
    return s


def dates(start: str, end: str):
    import datetime as dt
    d = dt.date(int(start[:4]), int(start[4:6]), int(start[6:]))
    e = dt.date(int(end[:4]), int(end[4:6]), int(end[6:]))
    while d <= e:
        yield d.strftime("%Y%m%d")
        d += dt.timedelta(days=1)


def main() -> None:
    start, end = sys.argv[1], sys.argv[2]
    total = 0
    for date in dates(start, end):
        raw = fetch_list(date)
        try:
            frag = json.loads(raw)
        except Exception:
            frag = raw
        ids = sorted(set(re.findall(r"umai_prof_goods_state_(b\d+_%d)" % YID, frag)))
        got = 0
        for gid in ids:
            try:
                fetch_detail(gid)
                got += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  !! {gid}: {exc}", flush=True)
        total += got
        print(f"{date} list={len(ids)} fetched={got} total={total}", flush=True)


if __name__ == "__main__":
    main()
