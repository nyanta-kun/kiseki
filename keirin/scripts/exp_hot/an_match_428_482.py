#!/usr/bin/env python3
"""482(極) のサンプル各レースに対応する 428(本体) の商品詳細を取得する。

同一 date+venue+race_no のペアを作り、両方の詳細を parse して JSONL 化する。
"""
from __future__ import annotations
import json
from pathlib import Path
from fetch_goods import fetch_detail
from parse_hot import parse_bets

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw" / "detail"
OUT = HERE / "prof"


def main() -> None:
    rows = [json.loads(l) for l in open(HERE / "month2.jsonl", encoding="utf-8")]
    def key(d): return (d["date"], d["venue"], d["race_no"])
    r428 = {key(d): d for d in rows if d["yid"] == 428}
    samp = [json.loads(l) for l in
            (OUT / "482_20260820_20260905.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"482 サンプル {len(samp)}件", flush=True)
    out = []
    for i, s in enumerate(samp):
        k = (s["date"], s["venue"], s["race_no"])
        m = r428.get(k)
        if not m:
            continue
        try:
            fetch_detail(m["gid"])
            p = parse_bets(RAW / f"{m['gid']}.html")
        except Exception as exc:                                   # noqa: BLE001
            print(f"!! {m['gid']}: {exc}", flush=True)
            continue
        out.append({"key": list(k),
                    "y482": {"gid": s["gid"], "comment": s.get("comment"),
                             "n_points": s.get("n_points_total"), "bet": s.get("bet"),
                             "payout": s.get("payout"), "bet_types": s.get("bet_types"),
                             "unit_min": s.get("unit_min"), "unit_max": s.get("unit_max"),
                             "cols": s.get("cols")},
                    "y428": {"gid": m["gid"], "comment": m.get("comment"),
                             "n_points": p["n_points_total"], "bet": m.get("bet"),
                             "payout": m.get("payout"), "bet_types": p["bet_types"],
                             "unit_min": p["unit_min"], "unit_max": p["unit_max"],
                             "cols": [r["cols"] for r in p["rows"]],
                             "modes": sorted({r["mode"] for r in p["rows"]})}})
        if (i + 1) % 40 == 0:
            print(f"  {i+1}/{len(samp)}", flush=True)
    q = OUT / "match_428_482.jsonl"
    q.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out), encoding="utf-8")
    print("->", q, len(out))


if __name__ == "__main__":
    main()
