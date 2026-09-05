#!/usr/bin/env python3
"""546(iAI -居合-) / 583(LONELYWOLF) の詳細HTMLを取得する（キャッシュ式）。

  1) 2026-08-20〜09-05 を stride で間引いたサンプル
  2) month2.jsonl の payout>=100,000 の全件
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from fetch_goods import fetch_detail

HERE = Path(__file__).resolve().parent
STRIDE = {546: 6, 583: 4}
WIN = (20260820, 20260905)

rows = [json.loads(l) for l in (HERE / "month2.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
targets: dict[str, dict] = {}
for yid in (546, 583):
    mine = [r for r in rows if r["yid"] == yid]
    mine.sort(key=lambda r: (r["date"], r["venue"], r["race_no"]))
    win = [r for r in mine if WIN[0] <= int(r["date"]) <= WIN[1]]
    for r in win[::STRIDE[yid]]:
        targets[r["gid"]] = r
    for r in mine:
        if (r.get("payout") or 0) >= 100000:
            targets[r["gid"]] = r
    print(yid, "window", len(win), "sample", len(win[::STRIDE[yid]]),
          "hi", sum(1 for r in mine if (r.get("payout") or 0) >= 100000), flush=True)
print("total targets", len(targets), flush=True)
for i, gid in enumerate(targets):
    try:
        fetch_detail(gid)
    except Exception as exc:  # noqa: BLE001
        print("!!", gid, exc, flush=True)
    if (i + 1) % 25 == 0:
        print(f"  {i+1}/{len(targets)}", flush=True)
print("done", flush=True)
