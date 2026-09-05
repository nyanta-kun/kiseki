#!/usr/bin/env python3
"""note の予想家「平八」の馬印記事を収集する（無料公開分のみ）。

`https://note.com/heihachi888` の公開 API を叩いて記事一覧と本文を取り、
馬印テーブルの画像（横長のほう。縦長は別ロジックの穴馬リスト）を保存する。

有料記事には触れない。price==0 の記事だけを対象にする。

  python3 scripts/heihachi/fetch_notes.py --out data/heihachi/raw

出力:
  <out>/notes_index.json  記事一覧（key/title/date/price）
  <out>/imgs/<date>_<key>.png  馬印テーブル画像
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

CREATOR = "heihachi888"
UA = "Mozilla/5.0"
SLEEP_SEC = 0.3  # 相手サーバーに負荷をかけない


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def fetch_index() -> list[dict]:
    """記事一覧を全ページ取得する。"""
    out: dict[str, dict] = {}
    page = 1
    while True:
        url = f"https://note.com/api/v2/creators/{CREATOR}/contents?kind=note&page={page}"
        data = json.loads(_get(url))["data"]
        for c in data["contents"]:
            out[c["key"]] = {
                "key": c["key"],
                "title": c["name"],
                "publish_date": c["publishAt"][:10],
                "price": c["price"],
            }
        if data.get("isLastPage"):
            break
        page += 1
        time.sleep(SLEEP_SEC)
    return sorted(out.values(), key=lambda r: r["publish_date"])


def title_date(title: str, publish_date: str) -> str:
    """タイトルの日付を優先する（前日に翌日ぶんを出すことがあるため）。"""
    m = re.search(r"(20\d\d)[/\-年](\d{1,2})[/\-月](\d{1,2})", title)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    return publish_date.replace("-", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/heihachi/raw")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "imgs").mkdir(parents=True, exist_ok=True)

    index = fetch_index()
    (out / "notes_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    targets = [r for r in index if r["price"] == 0 and "馬印" in r["title"]]
    print(f"記事 {len(index)} 件 / 無料の馬印 {len(targets)} 件")

    for i, r in enumerate(targets, 1):
        date = title_date(r["title"], r["publish_date"])
        dest = out / "imgs" / f"{date}_{r['key']}.png"
        if dest.exists():
            continue
        body = json.loads(_get(f"https://note.com/api/v3/notes/{r['key']}"))["data"]
        imgs = re.findall(
            r'<img src="([^"]+)"[^>]*width="(\d+)" height="(\d+)"', body.get("body") or ""
        )
        # 横長 = 馬印テーブル。縦長は別ロジックの穴馬リストなので取らない
        land = [u for u, w, h in imgs if int(w) > int(h)]
        if not land:
            print(f"  skip {date} {r['key']}: 横長画像なし")
            continue
        dest.write_bytes(_get(land[0]))
        time.sleep(SLEEP_SEC)
        if i % 20 == 0:
            print(f"  {i}/{len(targets)}")
    print("done")


if __name__ == "__main__":
    main()
