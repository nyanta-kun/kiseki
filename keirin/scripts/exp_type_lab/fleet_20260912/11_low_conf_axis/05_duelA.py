#!/usr/bin/env python3
"""3者対決 A — 現行プランのまま、プールだけ絞る（同一レース・同一買い方）。

基準は `all`（= ① 現行）。入稿ゲートを通った分だけで比べる（両腕ともゲート通過）。
Δ は レース単位 paired bootstrap 95%CI。件数を減らす腕には無作為対照20seed。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import lib as L

rows, thr = L.load()
POOLS = ("all", "hd_not_a1", "hd_a2", "hd_a23", "hd_mkt", "hd_pw",
         "bust", "bust_pw", "bust_mkt")
LAYERS = ["(全体)", "axis_sum_lo25", "p3_gap12_lo25", "pw_gap12_lo25",
          "pw_max_lo25", "pw_ent_hi10", "dis", "axis_sum_lo25+dis",
          "pw_max_lo25+dis", "p3_gap12_lo25+dis"]


def run(layer: str):
    print(f"\n{'='*118}\n## 層 = {layer}\n{'='*118}")
    for win in ("explore", "confirm"):
        nd = L.ndays(win)
        sub = [r for r in rows if r["win"] == win and L.inlay(r, layer)]
        base_ok = [r for r in sub if r["base"]["gate"]]
        if len(base_ok) < 60:
            print(f"[{win}] n不足 {len(base_ok)}")
            continue
        print(f"\n[{win}] 層のレース n={len(sub):,} / 現行がゲートを通る n={len(base_ok):,}"
              f" / 日数 {nd}")
        print(L.HEAD)
        for pn in POOLS:
            # 同一レース対比較: 両腕ともゲートを通ったレースだけ
            pr = [(r["arms"]["all"], r["arms"][pn]) for r in sub
                  if "all" in r["arms"] and pn in r["arms"]
                  and r["arms"]["all"]["gate"] and r["arms"][pn]["gate"]]
            if len(pr) < 40:
                print(f"  {pn:20s}  (対比較 n={len(pr)} 不足)")
                continue
            b = [x[0] for x in pr]; a = [x[1] for x in pr]
            sa = L.summ(a, nd)
            ds, lo, hi = L.paired(L.shown_vec(b), L.shown_vec(a))
            db, blo, bhi = L.paired(L.big_vec(b), L.big_vec(a))
            d3, t3lo, t3hi = L.paired(L.big_vec(b, 300_000), L.big_vec(a, 300_000))
            dr, rlo, rhi = L.roi_pair(b, a)
            tag = "(基準)" if pn == "all" else ""
            print(L.line(pn, sa) + f"  {tag}")
            if pn != "all":
                print(f"      Δ表示的中 {ds:+6.2f} [{lo:+6.2f},{hi:+6.2f}]"
                      f"  Δ10万+率 {db:+5.2f} [{blo:+5.2f},{bhi:+5.2f}]"
                      f"  Δ30万+率 {d3:+5.2f} [{t3lo:+5.2f},{t3hi:+5.2f}]"
                      f"  ΔROI {dr:+6.2f} [{rlo:+6.2f},{rhi:+6.2f}]  (対 n={len(pr)})")


if __name__ == "__main__":
    for lay in (sys.argv[1:] or LAYERS):
        run(lay)
