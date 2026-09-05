#!/usr/bin/env python3
"""担当3人（424 LONEFOX / 345 Aiライン極 / 410 倉本匠馬）の高額的中(払戻10万+)の
詳細HTMLを全件取得し、parse_hot で分解して JSONL へ落とす。

usage: python3 an_424_345_410_fetch.py
出力: prof/hi_<yid>.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fetch_goods import fetch_detail
from parse_hot import parse_bets

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
OUT = HERE / "prof"
OUT.mkdir(exist_ok=True)
YIDS = (424, 345, 410)
THRESH = 100_000


def main() -> None:
    """month2.jsonl から高額的中を拾い、詳細を取得して分解結果を保存する。"""
    by_yid: dict[int, list[dict]] = {y: [] for y in YIDS}
    for line in (HERE / "month2.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d["yid"] in by_yid and (d.get("payout") or 0) >= THRESH:
            by_yid[d["yid"]].append(d)
    for yid, items in by_yid.items():
        rows = []
        for i, it in enumerate(items):
            try:
                fetch_detail(it["gid"])
                p = parse_bets(RAW / "detail" / f"{it['gid']}.html")
            except Exception as exc:  # noqa: BLE001
                print(f"!! {it['gid']}: {exc}", flush=True)
                continue
            it.update({k: p.get(k) for k in
                       ("n_points_total", "unit_min", "unit_max", "bet_types",
                        "total_bet")})
            it["modes"] = sorted({r["mode"] for r in p["rows"]})
            it["rows_detail"] = p["rows"]
            it["hit"] = p["hit"]
            it["detail_payout"] = p["payout"]
            rows.append(it)
            print(f"  {yid} {i+1}/{len(items)}", flush=True)
        q = OUT / f"hi_{yid}.jsonl"
        q.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                     encoding="utf-8")
        print(f"-> {q} {len(rows)}件", flush=True)


if __name__ == "__main__":
    sys.exit(main())
