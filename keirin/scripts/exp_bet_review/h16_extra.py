#!/usr/bin/env python3
"""H16 補助: ②の hit 5商品だけの ΔROI（高額枠・日次上限の波及を外す）・目視1レース・2026 の比の中央値。"""
from __future__ import annotations
import pickle, sys, itertools
import numpy as np
import h16_report as H
from h16_run import *   # noqa
d = H.d
sel = lambda r: r["plan"] in H.HIT3
ac, a2 = H.agg(d["cur"], sel), H.agg(d["a2"], sel)
for lab, mk in (("全体", None), ("上期", H.H1D), ("下期", ~H.H1D)):
    r, rc = H.boot_delta(ac, a2, "roi", mk); s, sc = H.boot_delta(ac, a2, "shown", mk)
    print(f"hit5商品のみ ② − ① {lab}: ΔROI {r:+.2f} [{rc[0]:+.2f},{rc[1]:+.2f}]  Δ表示的中 {s:+.2f} [{sc[0]:+.2f},{sc[1]:+.2f}]")
mc, m2 = H.met(ac), H.met(a2)
print("hit5 ①", mc["n"], round(mc["roi"], 2), round(mc["shown"], 2), "② ", m2["n"], round(m2["roi"], 2), round(m2["shown"], 2))
# ② で売れた hit5 のうち 実際に三連複へ変えた(conv>0)レース / 変えなかった(4組以上) レース
conv = {(k, pk) for (arm, k, pk), v in d["info"].items() if arm == "a2" and v["conv"] > 0}
kc = {(r["race_key"], r["slot"], r["plan"]): r for r in d["cur"] if r["plan"] in H.HIT3}
ka = {(r["race_key"], r["slot"], r["plan"]): r for r in d["a2"] if r["plan"] in H.HIT3}
for lab, f in (("三連複に変更(≤3組)", lambda k: (k[0], k[2]) in conv), ("現行のまま(≥4組)", lambda k: (k[0], k[2]) not in conv)):
    both = [k for k in kc if k in ka and f(k)]
    lost = [k for k in kc if k not in ka and f(k)]
    ci = sum(kc[k]["inv"] for k in both); pi = sum(kc[k]["pay"] for k in both)
    ai = sum(ka[k]["inv"] for k in both); ap = sum(ka[k]["pay"] for k in both)
    li = sum(kc[k]["inv"] for k in lost); lp = sum(kc[k]["pay"] for k in lost)
    print(f"{lab}: 両腕で売れた {len(both)} ①{pi/ci*100:.2f} → ②{ap/ai*100:.2f} / 見送り{len(lost)}件 (①ROI {lp/li*100 if li else float('nan'):.2f})")
# ゲート落ちの内訳: ①で売ったが ② で見送った hit5 のうち ≤3組で変換したもの
print("② 変換した hit 構築数", len(conv), " うちゲート落ちで売れず", sum(1 for k in kc if k not in ka and (k[0], k[2]) in conv))
# 目視
INFO = d["info"]
z, idx = prep(); load_final()
cache = {int(i): S.ctx(int(i)) for i in idx}
want = None
for r in d["a2"]:
    if r["plan"] == "B_hit" and r["trio"] is not None and any(isinstance(k, frozenset) for k in r["stakes"]) and len(r["stakes"]) == 3:
        want = r["race_key"]; break
x = next(c for c in cache.values() if c and c.key == want)
print("目視:", want)
for arm in ("cur", "a2", "a3"):
    rr = [r for r in d[arm] if r["race_key"] == want]
    for r in rr:
        print(arm, r["plan"], "inv", r["inv"], "pay", r["pay"], "mean", round(r["mean"]), sorted(((tuple(sorted(k)) if isinstance(k, frozenset) else k), v) for k, v in r["stakes"].items()))
print("trio pred:", {tuple(sorted(g)): round(x.po_t3[g], 2) for g in {frozenset(p) for p in [k for r in d["cur"] if r["race_key"] == want for k in r["stakes"]]}})
print("win tf", x.win_tf, "tf odds", x.pay_tf, "win trio", sorted(x.win_t3), x.odds_t3)
