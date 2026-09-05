#!/usr/bin/env python3
"""担当4予想家の payout>=100,000 の商品を全件、詳細まで取得して JSONL 化する。"""
from __future__ import annotations
import json, sys
from pathlib import Path
from fetch_goods import fetch_detail
from parse_hot import parse_bets

RAW = Path(__file__).resolve().parent / "raw"
OUT = Path(__file__).resolve().parent / "prof"
OUT.mkdir(exist_ok=True)
YIDS = {614, 482, 428, 506}
THRESH = 100_000

def main() -> None:
    items = []
    for line in open("month2.jsonl", encoding="utf-8"):
        d = json.loads(line)
        if d["yid"] in YIDS and (d.get("payout") or 0) >= THRESH:
            items.append(d)
    print(f"対象 {len(items)}件", flush=True)
    rows = []
    for i, it in enumerate(items):
        try:
            fetch_detail(it["gid"])
            p = parse_bets(RAW / "detail" / f"{it['gid']}.html")
        except Exception as exc:                                   # noqa: BLE001
            print(f"!! {it['gid']}: {exc}", flush=True)
            continue
        it.update({k: p.get(k) for k in
                   ("n_points_total", "unit_min", "unit_max", "bet_types",
                    "total_bet")})
        it["payout_detail"] = p.get("payout")
        it["modes"] = sorted({r["mode"] for r in p["rows"]})
        it["rows_detail"] = p["rows"]
        it["hit"] = p["hit"]
        rows.append(it)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(items)}", flush=True)
    q = OUT / "hipay_614_482_428_506.jsonl"
    q.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print("->", q, len(rows))

if __name__ == "__main__":
    main()
