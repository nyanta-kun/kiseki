#!/usr/bin/env python3
"""1人の予想家の「商品構成」を全商品（外れ含む）から実測する。

usage: python3 profile.py <yid> <from> <to> [--limit N]

一覧API（キャッシュ）で商品を列挙 → 詳細HTMLを取得（キャッシュ）→ parse_hot で
1点=1行に展開し、券種・点数・1点賭け金・形式・的中倍率を JSONL へ落とす。

出力: prof/<yid>_<from>_<to>.jsonl  （1行=1商品）
"""
from __future__ import annotations
import json, statistics as st, sys
from pathlib import Path
from fetch_goods import fetch_list, fetch_detail
from fetch_month import days, parse_list
from parse_hot import parse_bets

RAW = Path(__file__).resolve().parent / "raw"
OUT = Path(__file__).resolve().parent / "prof"
OUT.mkdir(exist_ok=True)


def main() -> None:
    yid, a, b = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 10**9
    stride = int(sys.argv[sys.argv.index("--stride") + 1]) if "--stride" in sys.argv else 1
    items = []
    for d in days(a, b):
        try:
            items += parse_list(yid, d, json.loads(fetch_list(yid, d)))
        except Exception as exc:                                   # noqa: BLE001
            print(f"!! list {yid} {d}: {exc}", flush=True)
    print(f"商品 {len(items)}件（{a}〜{b}）", flush=True)
    rows = []
    items = items[::stride]
    for i, it in enumerate(items[:limit]):
        try:
            fetch_detail(it["gid"])
            d = parse_bets(RAW / "detail" / f"{it['gid']}.html")
        except Exception as exc:                                   # noqa: BLE001
            print(f"!! detail {it['gid']}: {exc}", flush=True)
            continue
        it.update({k: d.get(k) for k in
                   ("n_points_total", "unit_min", "unit_max", "bet_types", "total_bet")})
        it["modes"] = sorted({r["mode"] for r in d["rows"]})
        it["n_rows"] = len(d["rows"])
        it["hit"] = d["hit"]
        it["cols"] = [r["cols"] for r in d["rows"]]
        rows.append(it)
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{min(len(items), limit)}", flush=True)
    p = OUT / f"{yid}_{a}_{b}.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print("->", p, len(rows), "件")

    def med(xs):
        xs = [x for x in xs if x is not None]
        return st.median(xs) if xs else None
    hits = [r for r in rows if r.get("payout")]
    print(f"  券種      : {sorted({t for r in rows for t in (r['bet_types'] or [])})}")
    print(f"  点数 中央 : {med([r['n_points_total'] for r in rows])}  "
          f"範囲 {min([r['n_points_total'] or 0 for r in rows] or [0])}"
          f"〜{max([r['n_points_total'] or 0 for r in rows] or [0])}")
    print(f"  1点賭金   : min中央 {med([r['unit_min'] for r in rows])} / "
          f"max中央 {med([r['unit_max'] for r in rows])}")
    print(f"  購入額中央: {med([r['bet'] for r in rows])}")
    print(f"  的中      : {len(hits)}/{len(rows)} = {len(hits)/max(len(rows),1)*100:.1f}%")
    if hits:
        print(f"  払戻中央  : {med([r['payout'] for r in hits])}  "
              f"最大 {max(r['payout'] for r in hits)}")
    inv = sum(r["bet"] or 0 for r in rows)
    pay = sum(r["payout"] or 0 for r in rows)
    print(f"  ROI       : {pay/inv*100:.1f}%  (投資 {inv:,} / 払戻 {pay:,})")


if __name__ == "__main__":
    main()
