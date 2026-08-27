"""指定 (yosoka_id, 開催日) の商品一覧 JSON と、指定商品の詳細 HTML を保存する。

exp_gensen/fetch_gensen.py と同じ API・同じキャッシュ方式。
"""
from __future__ import annotations
import json, sys, time, urllib.parse, urllib.request
from pathlib import Path

BASE = "https://keirin.netkeiba.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ROOT = Path(__file__).resolve().parent / "raw"
(ROOT / "list").mkdir(parents=True, exist_ok=True)
(ROOT / "detail").mkdir(parents=True, exist_ok=True)
INTERVAL = 0.8


def _get(url: str, data: bytes | None = None) -> str:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def fetch_list(yid: int, date: str) -> str:
    p = ROOT / "list" / f"{yid}_{date}.json"
    if p.exists():
        return p.read_text(encoding="utf-8")
    body = urllib.parse.urlencode({
        "input": "UTF-8", "output": "json", "show_id": "goods_list_main",
        "kaisai_date": date, "yosoka_id": yid, "jyo": "all"}).encode()
    s = _get(f"{BASE}/yoso/api/api_get_goods_list_prof.html", body)
    p.write_text(s, encoding="utf-8")
    time.sleep(INTERVAL)
    return s


def fetch_detail(gid: str) -> str:
    p = ROOT / "detail" / f"{gid}.html"
    if p.exists():
        return p.read_text(encoding="utf-8")
    s = _get(f"{BASE}/yoso/detail/?id={gid}")
    p.write_text(s, encoding="utf-8")
    time.sleep(INTERVAL)
    return s


if __name__ == "__main__":
    print(fetch_list(int(sys.argv[1]), sys.argv[2])[:2000])
