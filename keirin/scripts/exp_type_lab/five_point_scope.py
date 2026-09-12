#!/usr/bin/env python3
"""少点数化を「どのプランに」当てるか（2026-09-10）。

🔴 全プランへ当てると、効きの大半は **A_ana（穴狙い）と F_sign（看板枠）を
   本命買いに作り替えたこと**から来る。両者は設計上 表示的中 3〜7% の一撃商品で、
   当てにいく商品へ替えれば的中は必ず上がる。**2026-09-08 に型Eの帯下差込を
   「商品性」を理由に見送ったのと同じ取引**なので、分けて測る。
"""
from __future__ import annotations

import pickle
import numpy as np

ROWS = pickle.load(open("/tmp/five_point_rows.pkl", "rb"))
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}
sp_ex = np.array([r["sp5"] for r in W["explore"]])
THR = float(np.percentile(sp_ex, 50))

SCOPES = {
    "① 全プラン": None,
    "② 一撃商品を除く": {"A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"},
    "③ B_hit だけ": {"B_hit"},
    "④ B_hit + E_hit": {"B_hit", "E_hit"},
    "⑤ B_hit + E_hit + D_hit": {"B_hit", "E_hit", "D_hit"},
}


def pick(r, scope, use_thr=True, forced=None):
    sw = (forced if forced is not None
          else (r["sp5"] >= THR and (scope is None or r["plan"] in scope)))
    if sw:
        a = r["arms"]["k5_noband"]
        if a and a["gate"]:
            return a, True
    b = r["arms"]["cur"]
    return (b, False) if (b and b["gate"]) else (None, False)


def agg(sel, nd):
    inv = sum(a["inv"] for a in sel); pay = sum(a["pay"] for a in sel)
    pays = sorted(a["pay"] for a in sel if a["pay"] > 0)
    return dict(perday=len(sel) / nd, k=np.mean([a["k"] for a in sel]),
                hit=np.mean([a["pay"] > 0 for a in sel]) * 100,
                shown=np.mean([a["pay"] >= a["inv"] for a in sel]) * 100,
                roi=pay / inv * 100, med=float(np.median(pays)) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / nd)


def boot(a, b, iters=1000, seed=0):
    rng = np.random.default_rng(seed)
    as_ = np.array([x["pay"] >= x["inv"] for x in a], float)
    bs = np.array([x["pay"] >= x["inv"] for x in b], float)
    ap = np.array([x["pay"] for x in a]); ai = np.array([x["inv"] for x in a])
    bp = np.array([x["pay"] for x in b]); bi = np.array([x["inv"] for x in b])
    n = len(as_); ds, dr = [], []
    for _ in range(iters):
        j = rng.integers(0, n, n)
        ds.append((as_[j].mean() - bs[j].mean()) * 100)
        dr.append(ap[j].sum() / ai[j].sum() * 100 - bp[j].sum() / bi[j].sum() * 100)
    f = lambda v: (np.mean(v), np.percentile(v, 2.5), np.percentile(v, 97.5))
    return f(ds), f(dr)


def main() -> None:
    print(f"Σp5 の閾値（探索窓 p50）= {THR:.4f}")
    for wn, lab in (("confirm", "確認 2026-01〜08（本番相当）"),
                    ("explore", "探索 2024-07〜2025-12")):
        rows = W[wn]
        nd = len({r["date"] for r in rows})
        base = [x for x, _ in (pick(r, set()) for r in rows) if x]
        B = agg(base, nd)
        print("\n" + "=" * 116)
        print(f"=== {lab}  {nd}日")
        print("=" * 116)
        print(f"    {'腕':24s} {'件/日':>6s} {'点数':>5s} {'的中%':>7s} {'表示的中%':>9s} "
              f"{'ROI%':>7s} {'払戻中央':>9s} {'10万+/日':>8s}  {'替えた/日':>8s}"
              f"   {'現行との差 95%CI（表示的中 / ROI）':>36s}")
        print(f"    {'現行':24s} {B['perday']:6.2f} {B['k']:5.1f} {B['hit']:7.2f}"
              f" {B['shown']:9.2f} {B['roi']:7.1f} {B['med']:9,.0f} {B['big']:8.3f}")
        for name, scope in SCOPES.items():
            sel, pa, pb, nsw = [], [], [], 0
            for r in rows:
                x, sw = pick(r, scope)
                y, _ = pick(r, set())
                if x:
                    sel.append(x); nsw += sw
                if x and y:
                    pa.append(x); pb.append(y)
            S = agg(sel, nd)
            (ds, dl, dh), (rs, rl, rh) = boot(pa, pb)
            print(f"    {name:24s} {S['perday']:6.2f} {S['k']:5.1f} {S['hit']:7.2f}"
                  f" {S['shown']:9.2f} {S['roi']:7.1f} {S['med']:9,.0f} {S['big']:8.3f}"
                  f"  {nsw/nd:8.2f}   {ds:+5.2f}pt [{dl:+5.2f},{dh:+5.2f}]  "
                  f"{rs:+5.1f} [{rl:+5.1f},{rh:+5.1f}]")

        # 無作為対照: 同じプラン母集団から、同じ件数を無作為に切り替える
        print("\n    ■ 無作為対照20本（同じプラン群の中から同数を無作為に5点へ）")
        for name, scope in list(SCOPES.items())[1:]:
            elig = [j for j, r in enumerate(rows)
                    if (scope is None or r["plan"] in scope)
                    and r["arms"]["k5_noband"] and r["arms"]["k5_noband"]["gate"]]
            nsw = sum(1 for j in elig if rows[j]["sp5"] >= THR)
            if not nsw or nsw >= len(elig):
                continue
            sel = [x for x, _ in (pick(r, scope) for r in rows) if x]
            S = agg(sel, nd)
            cs, cr = [], []
            for seed in range(20):
                rng = np.random.default_rng(seed)
                sw = set(rng.choice(elig, size=nsw, replace=False).tolist())
                v = [x for x, _ in (pick(r, scope, forced=(j in sw))
                                    for j, r in enumerate(rows)) if x]
                V = agg(v, nd)
                cs.append(V["shown"]); cr.append(V["roi"])
            print(f"      {name:24s} 表示的中 {S['shown']:5.2f} ↔ 対照中央 {np.median(cs):5.2f}"
                  f"  勝ち {sum(S['shown']>c for c in cs):2d}/20    "
                  f"ROI {S['roi']:5.1f} ↔ {np.median(cr):5.1f}  勝ち {sum(S['roi']>c for c in cr):2d}/20")


if __name__ == "__main__":
    main()
