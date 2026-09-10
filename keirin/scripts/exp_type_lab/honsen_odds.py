#!/usr/bin/env python3
"""本線（三連複 ◎○△）の予測オッズで、見送りと買い足しを決められるか（2026-09-10）。

> ユーザー仮説: ◎○△が1番人気で2〜3倍以下なら、波乱狙い以外はレースごと見送る。
> 逆に6倍を超えるなら順位入れ替えを含めて推奨に入れる。

本線＝**三連複 {◎○△} の予測オッズ**（実測分位: p25=2.6 / 中央3.7 / p75=5.0 / p90=6.8。
3倍以下の 97〜100% が予測1番人気なので「1番人気で2〜3倍」はこの量と一致する）。

🔴 見送りは件数を減らすので**無作為対照20本**を置く（`docs/RECOMMENDATION.md` §6）。
🔴 波乱狙い（`A_ana` / `F_sign`）は見送りの対象外＝そのまま売る（ユーザー指定）。
"""
from __future__ import annotations

import pickle
import numpy as np

ROWS = pickle.load(open("/tmp/mark_order_fix_rows2.pkl", "rb"))
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}
BY_DESIGN = {"A_ana", "F_sign"}
ADD_SCOPE = {"B_hit", "E_hit", "D_hit", "A_hit", "F_hit", "C_hit"}


def agg(sel, nd):
    if not sel:
        return dict(perday=0, k=0, shown=0, roi=0, med=0, big=0, n=0)
    inv = sum(a["inv"] for a in sel); pay = sum(a["pay"] for a in sel)
    pays = sorted(a["pay"] for a in sel if a["pay"] > 0)
    return dict(n=len(sel), perday=len(sel) / nd, k=np.mean([a["k"] for a in sel]),
                shown=np.mean([a["pay"] >= a["inv"] for a in sel]) * 100,
                roi=pay / inv * 100, med=float(np.median(pays)) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / nd)


def boot(a, b, iters=1000, seed=0):
    rng = np.random.default_rng(seed)
    A = np.array([x["pay"] >= x["inv"] for x in a], float)
    B = np.array([x["pay"] >= x["inv"] for x in b], float)
    ap = np.array([x["pay"] for x in a]); ai = np.array([x["inv"] for x in a])
    bp = np.array([x["pay"] for x in b]); bi = np.array([x["inv"] for x in b])
    n = len(A); ds, dr = [], []
    for _ in range(iters):
        j = rng.integers(0, n, n)
        ds.append((A[j].mean() - B[j].mean()) * 100)
        dr.append(ap[j].sum() / ai[j].sum() * 100 - bp[j].sum() / bi[j].sum() * 100)
    f = lambda v: (np.mean(v), np.percentile(v, 2.5), np.percentile(v, 97.5))
    return f(ds), f(dr)


BANDS = [(0, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 8), (8, 1e9)]


def main() -> None:
    for wn, lab in (("confirm", "確認 2026-01〜08（本番相当）"),
                    ("explore", "探索 2024-07〜2025-12")):
        rows = [r for r in W[wn] if r["arms"]["cur"]["gate"] and np.isfinite(r["hs_trio"])]
        nd = len({r["date"] for r in rows})
        print("\n" + "=" * 116)
        print(f"=== {lab}   {len(rows):,}商品 / {nd}日")
        print("=" * 116)

        # ① 本線オッズ帯の実態 ─────────────────────────────────────────
        print("\n  ■ 本線（三連複◎○△）の予測オッズ帯ごとの実態")
        print(f"    {'帯':10s} {'件/日':>6s} {'割合%':>6s} {'◎○△決着%':>9s} "
              f"{'現行 表示的中%':>13s} {'ROI%':>7s} {'払戻中央':>9s} {'10万+/日':>8s} {'穴狙い%':>7s}")
        for lo, hi in BANDS:
            sub = [r for r in rows if lo <= r["hs_trio"] < hi]
            if not sub:
                continue
            s = agg([r["arms"]["cur"] for r in sub], nd)
            m3 = np.mean([r["m3_hit"] for r in sub]) * 100
            by = np.mean([r["plan"] in BY_DESIGN for r in sub]) * 100
            name = f"{lo}〜{hi}倍" if hi < 1e9 else f"{lo}倍〜"
            print(f"    {name:10s} {s['perday']:6.2f} {len(sub)/len(rows)*100:6.2f}"
                  f" {m3:9.2f} {s['shown']:13.2f} {s['roi']:7.1f} {s['med']:9,.0f}"
                  f" {s['big']:8.3f} {by:7.1f}")

        base = [r["arms"]["cur"] for r in rows]
        B = agg(base, nd)

        # ② 見送りルール ───────────────────────────────────────────────
        print(f"\n  ■ 本線が安いレースを見送る（波乱狙い {sorted(BY_DESIGN)} は残す）")
        print(f"    {'腕':24s} {'件/日':>6s} {'表示的中%':>9s} {'ROI%':>7s} {'払戻中央':>9s} "
              f"{'10万+/日':>8s} {'落とす/日':>8s}   {'無作為対照20本（同数を無作為に見送り）':>44s}")
        print(f"    {'⓪ 現行':24s} {B['perday']:6.2f} {B['shown']:9.2f} {B['roi']:7.1f}"
              f" {B['med']:9,.0f} {B['big']:8.3f}")
        for T in (2.0, 2.5, 3.0, 3.5, 4.0):
            keep = [r for r in rows if r["hs_trio"] >= T or r["plan"] in BY_DESIGN]
            drop = len(rows) - len(keep)
            s = agg([r["arms"]["cur"] for r in keep], nd)
            cs, cr = [], []
            for seed in range(20):
                rng = np.random.default_rng(seed)
                pick = set(rng.choice(len(rows), size=len(keep), replace=False).tolist())
                u = agg([r["arms"]["cur"] for j, r in enumerate(rows) if j in pick], nd)
                cs.append(u["shown"]); cr.append(u["roi"])
            print(f"    {f'本線 {T}倍未満を見送り':24s} {s['perday']:6.2f} {s['shown']:9.2f}"
                  f" {s['roi']:7.1f} {s['med']:9,.0f} {s['big']:8.3f} {drop/nd:8.2f}"
                  f"   表示的中 {sum(s['shown']>c for c in cs):2d}/20(中{np.median(cs):5.2f})"
                  f"  ROI {sum(s['roi']>c for c in cr):2d}/20(中{np.median(cr):5.1f})")

        # ③ 本線が高いときだけ買い足す ─────────────────────────────────
        print("\n  ■ 本線が高いレースだけ ◎○△ の並びを買い足す（件数は不変）")
        print(f"    {'腕':28s} {'件/日':>6s} {'点数':>5s} {'表示的中%':>9s} {'ROI%':>7s} "
              f"{'払戻中央':>9s} {'10万+/日':>8s} {'維持/破壊/救済':>16s}   {'現行との差 95%CI':>30s}")
        for T in (0.0, 4.0, 5.0, 6.0, 7.0):
            for m in (1, 2, 3):
                sel, pa, pb, keep, brk, sav = [], [], [], 0, 0, 0
                for r in rows:
                    y = r["arms"]["cur"]
                    a = r["arms"].get(f"add{m}")
                    x = (a if (a and a["gate"] and r["hs_trio"] >= T
                               and r["plan"] in ADD_SCOPE) else y)
                    sel.append(x); pa.append(x); pb.append(y)
                    hx, hy = x["pay"] >= x["inv"], y["pay"] >= y["inv"]
                    keep += hx and hy; brk += hy and not hx; sav += hx and not hy
                s = agg(sel, nd)
                (ds, dl, dh), (rs, rl, rh) = boot(pa, pb)
                nm = (f"本線 {T}倍以上に +{m}点" if T else f"全レースに +{m}点")
                print(f"    {nm:28s} {s['perday']:6.2f} {s['k']:5.1f} {s['shown']:9.2f}"
                      f" {s['roi']:7.1f} {s['med']:9,.0f} {s['big']:8.3f}"
                      f" {keep:5,}/{brk:3,}/{sav:4,}"
                      f"   {ds:+5.2f}pt [{dl:+5.2f},{dh:+5.2f}] {rs:+5.1f} [{rl:+5.1f},{rh:+5.1f}]")

        # ④ 組み合わせ ────────────────────────────────────────────────
        print("\n  ■ 組み合わせ（安い側は見送り × 高い側は買い足し）")
        for T_lo, T_hi, m in ((2.5, 6.0, 1), (3.0, 6.0, 1), (3.0, 6.0, 2), (3.0, 5.0, 2)):
            sel = []
            for r in rows:
                if r["hs_trio"] < T_lo and r["plan"] not in BY_DESIGN:
                    continue
                y = r["arms"]["cur"]
                a = r["arms"].get(f"add{m}")
                sel.append(a if (a and a["gate"] and r["hs_trio"] >= T_hi
                                 and r["plan"] in ADD_SCOPE) else y)
            s = agg(sel, nd)
            print(f"    {f'{T_lo}倍未満は見送り / {T_hi}倍以上は +{m}点':36s} "
                  f"件/日 {s['perday']:6.2f}  表示的中 {s['shown']:6.2f}%  ROI {s['roi']:5.1f}"
                  f"  払戻中央 {s['med']:8,.0f}  10万+ {s['big']:.3f}")


if __name__ == "__main__":
    main()
