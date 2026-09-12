"""§2 同一日・同一節の相関。"""
import sys; sys.path.insert(0,'scripts/exp_type_lab'); 
import numpy as np
from collections import defaultdict
import daily_portfolio_ch1 as _c1
fit_p=_c1.fit_p
import daily_portfolio as D
rng = np.random.default_rng(7)

def pair_corr(rows, p, same):
    """same(r1,r2)->bool のペアだけで残差相関を出す。"""
    byday = defaultdict(list)
    for j, r in enumerate(rows):
        byday[r["date"]].append(j)
    num = den = 0.0; npair = 0
    for dd, jj in byday.items():
        for a in range(len(jj)):
            i = jj[a]
            ri, pi = rows[i], p[i]
            for b in range(a + 1, len(jj)):
                k = jj[b]
                if not same(ri, rows[k]):
                    continue
                pk = p[k]
                num += (ri["shown"] - pi) * (rows[k]["shown"] - pk)
                den += np.sqrt(pi * (1 - pi) * pk * (1 - pk))
                npair += 1
    return (num / den if den else 0.0), npair

def boot_ci(rows, p, same, B=400):
    """日単位ブートストラップの95%CI。"""
    byday = defaultdict(list)
    for j, r in enumerate(rows):
        byday[r["date"]].append(j)
    days = list(byday)
    vals = []
    for _ in range(B):
        pick = rng.choice(len(days), len(days), replace=True)
        num = den = 0.0
        for q in pick:
            jj = byday[days[q]]
            for a in range(len(jj)):
                i = jj[a]; ri, pi = rows[i], p[i]
                for b in range(a + 1, len(jj)):
                    k = jj[b]
                    if not same(ri, rows[k]):
                        continue
                    pk = p[k]
                    num += (ri["shown"] - pi) * (rows[k]["shown"] - pk)
                    den += np.sqrt(pi * (1 - pi) * pk * (1 - pk))
        vals.append(num / den if den else 0.0)
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))

SAME = [
    ("同じ日のすべてのペア", lambda a, b: True),
    ("  うち同じ節（会場×開催）", lambda a, b: a["cup"] == b["cup"]),
    ("  うち別の節", lambda a, b: a["cup"] != b["cup"]),
    ("  うち同じ節×隣接R(±2)", lambda a, b: a["cup"] == b["cup"]
        and abs(int(a["race_key"][-2:]) - int(b["race_key"][-2:])) <= 2),
]
for nm, rows in (("探索 2024-07〜2025-12", D.EX), ("確認 2026-01〜08", D.CF)):
    sold = D.produce(rows)
    p = fit_p(sold, lambda r: r["plan"])
    print(f"\n■ {nm}  {len(sold):,}件")
    print(f"  {'ペアの種類':28s} {'ペア数':>9s} {'残差相関ρ':>10s} {'95%CI':>22s} {'φへの寄与':>10s}")
    from collections import Counter
    nbar = float(np.mean(list(Counter(r['date'] for r in sold).values())))
    for lab, f in SAME:
        rho, npair = pair_corr(sold, p, f)
        lo, hi = boot_ci(sold, p, f, 300)
        print(f"  {lab:28s} {npair:9,} {rho:10.4f} [{lo:+.4f},{hi:+.4f}]"
              f" {1 + (nbar - 1) * rho if lab.startswith('同じ日') else float('nan'):10.3f}")
