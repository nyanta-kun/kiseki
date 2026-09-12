#!/usr/bin/env python3
"""3者対決 B — signboard で **計画払戻 T を揃えた**同一レース対比較。

T=150,000 が主たる比較点（ユーザーが選んだ計画払戻）。400,000 も参考で出す。
基準は `S:all@T`（= 本番 `*_sign` と同一構造）。
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib as L

rows, thr = L.load()
POOLS = ("all", "bust", "bust_pw", "hd_not_a1", "hd_a2", "hd_mkt")
LAYERS = ["(全体)", "axis_sum_lo25", "p3_gap12_lo25", "pw_gap12_lo25",
          "pw_max_lo25", "pw_ent_hi10", "dis", "axis_sum_lo25+dis"]


def run(T: int, layer: str):
    print(f"\n{'='*120}\n## T={T:,} / 層 = {layer}\n{'='*120}")
    for win in ("explore", "confirm"):
        nd = L.ndays(win)
        sub = [r for r in rows if r["win"] == win and L.inlay(r, layer)]
        bk = f"S:all@{T}"
        pr0 = [r for r in sub if (a := r["arms"].get(bk)) and a["gate"]]
        if len(pr0) < 60:
            print(f"[{win}] n不足")
            continue
        print(f"\n[{win}] 対比較の母集団 n={len(pr0):,} / 日数 {nd}")
        print(L.HEAD)
        for pn in POOLS:
            k = f"S:{pn}@{T}"
            pr = [(r["arms"][bk], r["arms"][k]) for r in sub
                  if (x := r["arms"].get(bk)) and x["gate"]
                  and (y := r["arms"].get(k)) and y["gate"]]
            if len(pr) < 40:
                print(f"  {pn:20s}  (対比較 n={len(pr)} 不足)")
                continue
            b = [x[0] for x in pr]; a = [x[1] for x in pr]
            print(L.line(pn, L.summ(a, nd)) + ("  (基準)" if pn == "all" else ""))
            if pn == "all":
                continue
            ds, lo, hi = L.paired(L.shown_vec(b), L.shown_vec(a))
            db, blo, bhi = L.paired(L.big_vec(b), L.big_vec(a))
            d3, t3lo, t3hi = L.paired(L.big_vec(b, 300_000), L.big_vec(a, 300_000))
            dr, rlo, rhi = L.roi_pair(b, a)
            print(f"      Δ表示的中 {ds:+6.2f} [{lo:+6.2f},{hi:+6.2f}]"
                  f"  Δ10万+率 {db:+5.2f} [{blo:+5.2f},{bhi:+5.2f}]"
                  f"  Δ30万+率 {d3:+5.2f} [{t3lo:+5.2f},{t3hi:+5.2f}]"
                  f"  ΔROI {dr:+6.2f} [{rlo:+6.2f},{rhi:+6.2f}]  (対 n={len(pr)})")


if __name__ == "__main__":
    Ts = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["150000"])]
    lays = sys.argv[2].split(",") if len(sys.argv) > 2 else LAYERS
    for T in Ts:
        for lay in lays:
            run(T, lay)
