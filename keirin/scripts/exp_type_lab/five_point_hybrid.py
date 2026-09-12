#!/usr/bin/env python3
"""少点数への切り替えは「どのレースか」で符号が変わる（2026-09-10）。

① Σp5 十分位ごとに、**同一レース**で 現行 ↔ 5点（帯なし）を突き合わせる。
② その交互作用を使ったハイブリッド（Σp5 が高い側だけ5点・他は現行）を測る。
🔴 対照は「同じ件数のレースを**無作為に選んで**5点へ替える」20本。
"""
from __future__ import annotations

import pickle
import numpy as np

ROWS = pickle.load(open("/tmp/five_point_rows.pkl", "rb"))
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}
THR = None


def pick(r, use5: bool):
    """そのレースで実際に売る商品（ゲートを通らなければ None）。"""
    a = r["arms"]["k5_noband"] if use5 else r["arms"]["cur"]
    if a and a["gate"]:
        return a
    # 🔴 5点でゲートに落ちたら**現行へ落とす**（売らないのではなく元の商品を売る）。
    b = r["arms"]["cur"]
    return b if (b and b["gate"]) else None


def agg(sel, nd):
    if not sel:
        return None
    inv = sum(a["inv"] for a in sel); pay = sum(a["pay"] for a in sel)
    shown = [a for a in sel if a["pay"] >= a["inv"]]
    pays = sorted(a["pay"] for a in sel if a["pay"] > 0)
    return dict(n=len(sel), perday=len(sel) / nd, k=np.mean([a["k"] for a in sel]),
                hit=np.mean([a["pay"] > 0 for a in sel]) * 100,
                shown=len(shown) / len(sel) * 100, roi=pay / inv * 100,
                med=float(np.median(pays)) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / nd)


def boot(sel_a, sel_b, iters=1000, seed=0):
    """レース単位ブートストラップ（対応あり）で (Δ表示的中, ΔROI) の95%CI。"""
    rng = np.random.default_rng(seed)
    a_s = np.array([x["pay"] >= x["inv"] for x in sel_a], float)
    b_s = np.array([x["pay"] >= x["inv"] for x in sel_b], float)
    a_p = np.array([x["pay"] for x in sel_a]); a_i = np.array([x["inv"] for x in sel_a])
    b_p = np.array([x["pay"] for x in sel_b]); b_i = np.array([x["inv"] for x in sel_b])
    n = len(a_s)
    ds, dr = [], []
    for _ in range(iters):
        j = rng.integers(0, n, n)
        ds.append((a_s[j].mean() - b_s[j].mean()) * 100)
        dr.append(a_p[j].sum() / a_i[j].sum() * 100 - b_p[j].sum() / b_i[j].sum() * 100)
    q = lambda v: (float(np.mean(v)), float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return q(ds), q(dr)


def main() -> None:
    global THR
    ex = W["explore"]
    sp = np.array([r["sp5"] for r in ex])
    THR = {q: float(np.percentile(sp, q)) for q in (30, 40, 50, 60, 70)}

    for wn, lab in (("confirm", "確認 2026-01〜08（本番相当）"),
                    ("explore", "探索 2024-07〜2025-12")):
        rows = W[wn]
        nd = len({r["date"] for r in rows})
        print("\n" + "=" * 112)
        print(f"=== {lab}   {nd}日")
        print("=" * 112)

        # ① Σp5 十分位 × 同一レース対比 ────────────────────────────────
        both = [r for r in rows if r["arms"]["cur"] and r["arms"]["cur"]["gate"]
                and r["arms"]["k5_noband"] and r["arms"]["k5_noband"]["gate"]]
        sp5 = np.array([r["sp5"] for r in both])
        e = [np.percentile(sp5, 10 * j) for j in range(1, 10)]
        d = np.digitize(sp5, e)
        print("\n  ■ 同一レースで 現行 ↔ 5点（帯なし）  Σp5 十分位ごと")
        print(f"    {'十分位':7s} {'n':>6s} {'現行 表示的中':>12s} {'5点 表示的中':>12s} "
              f"{'差':>8s} {'現行ROI':>8s} {'5点ROI':>8s} {'差':>7s}")
        for k in range(10):
            sub = [r for r, x in zip(both, d) if x == k]
            if len(sub) < 50:
                continue
            a = [r["arms"]["cur"] for r in sub]; b = [r["arms"]["k5_noband"] for r in sub]
            A, B = agg(a, nd), agg(b, nd)
            print(f"    D{k+1:<6d} {len(sub):6,} {A['shown']:12.2f} {B['shown']:12.2f} "
                  f"{B['shown']-A['shown']:+8.2f} {A['roi']:8.1f} {B['roi']:8.1f} "
                  f"{B['roi']-A['roi']:+7.1f}")

        # ② ハイブリッド ────────────────────────────────────────────────
        print("\n  ■ ラインナップ全体（Σp5 が閾値以上のレースだけ5点へ・他は現行のまま）")
        print(f"    {'腕':26s} {'件/日':>6s} {'点数':>5s} {'的中%':>7s} {'表示的中%':>9s} "
              f"{'ROI%':>7s} {'払戻中央':>9s} {'10万+/日':>8s}   {'現行との差 95%CI':>34s}")
        base = [x for x in (pick(r, False) for r in rows) if x]
        B0 = agg(base, nd)
        print(f"    {'現行':26s} {B0['perday']:6.2f} {B0['k']:5.1f} {B0['hit']:7.2f}"
              f" {B0['shown']:9.2f} {B0['roi']:7.1f} {B0['med']:9,.0f} {B0['big']:8.3f}")
        for q in (30, 40, 50, 60, 70):
            t = THR[q]
            sel, pair_a, pair_b = [], [], []
            for r in rows:
                x = pick(r, r["sp5"] >= t)
                y = pick(r, False)
                if x:
                    sel.append(x)
                if x and y:
                    pair_a.append(x); pair_b.append(y)
            S = agg(sel, nd)
            (ds, dl, dh), (rs, rl, rh) = boot(pair_a, pair_b)
            n_sw = sum(1 for r in rows if r["sp5"] >= t
                       and r["arms"]["k5_noband"] and r["arms"]["k5_noband"]["gate"])
            print(f"    {f'Σp5 上位{100-q}% を5点へ':26s} {S['perday']:6.2f} {S['k']:5.1f}"
                  f" {S['hit']:7.2f} {S['shown']:9.2f} {S['roi']:7.1f} {S['med']:9,.0f}"
                  f" {S['big']:8.3f}   {ds:+5.2f}pt [{dl:+5.2f},{dh:+5.2f}]  "
                  f"{rs:+5.1f} [{rl:+5.1f},{rh:+5.1f}]  (替えた {n_sw/nd:.1f}件/日)")

        # ③ 無作為対照: 同数を無作為に5点へ替える ────────────────────────
        print("\n  ■ 無作為対照20本（同じ件数のレースを無作為に選んで5点へ替える）")
        for q in (50, 70):
            t = THR[q]
            swid = [j for j, r in enumerate(rows) if r["sp5"] >= t
                    and r["arms"]["k5_noband"] and r["arms"]["k5_noband"]["gate"]]
            sel = [x for x in (pick(r, r["sp5"] >= t) for r in rows) if x]
            S = agg(sel, nd)
            cand = [j for j, r in enumerate(rows)
                    if r["arms"]["k5_noband"] and r["arms"]["k5_noband"]["gate"]]
            cs, cr = [], []
            for seed in range(20):
                rng = np.random.default_rng(seed)
                sw = set(rng.choice(cand, size=len(swid), replace=False).tolist())
                v = [x for x in (pick(r, j in sw) for j, r in enumerate(rows)) if x]
                V = agg(v, nd)
                cs.append(V["shown"]); cr.append(V["roi"])
            print(f"    Σp5 上位{100-q}%  表示的中 {S['shown']:5.2f}% ↔ 対照中央 "
                  f"{np.median(cs):5.2f}%  勝ち {sum(S['shown']>c for c in cs):2d}/20"
                  f"   ROI {S['roi']:5.1f} ↔ {np.median(cr):5.1f}  "
                  f"勝ち {sum(S['roi']>c for c in cr):2d}/20")


if __name__ == "__main__":
    main()
