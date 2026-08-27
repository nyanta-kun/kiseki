"""netkeiba 競輪「好調予想家」ページ（/yoso/hot/）の生HTMLを保存する。

作法: 生HTMLは必ずローカルへ保存してから解析する（exp_gensen と同じ）。
"""
from __future__ import annotations
import sys, urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ROOT = Path(__file__).resolve().parent / "raw"
ROOT.mkdir(parents=True, exist_ok=True)


def get(url: str, name: str) -> str:
    p = ROOT / name
    if p.exists():
        return p.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        s = r.read().decode("euc_jp", "replace")
    if "<html" not in s.lower() or s.count("�") > len(s) * 0.02:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            s = r.read().decode("utf-8", "replace")
    p.write_text(s, encoding="utf-8")
    return s


if __name__ == "__main__":
    s = get("https://keirin.netkeiba.com/yoso/hot/?rf=topk", "hot.html")
    print(len(s), "chars ->", ROOT / "hot.html")
