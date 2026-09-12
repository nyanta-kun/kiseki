"""§7 「同じ節に商品が集中する日」は構成期待から下振れするか（唯一 両窓で 0 を跨がなかった量）。"""
import sys; sys.path.insert(0,'scripts/exp_type_lab')
import numpy as np
from collections import defaultdict
import daily_portfolio as D
rng = np.random.default_rng(3)

for nm, rows in (("探索 2024-07〜2025-12", D.EX), ("確認 2026-01〜08", D.CF)):
    sold = D.produce(rows)
    base = {}
    for p in {r['plan'] for r in sold}:
        s = [r for r in sold if r['plan'] == p]
        base[p] = sum(1 for r in s if r['pay'] > r['inv']) / len(s)
    dd = defaultdict(list)
    for r in sold:
        dd[r['date']].append(r)
    D_ = []
    for d, s in dd.items():
        n = len(s); e = sum(base[r['plan']] for r in s)
        o = sum(1 for r in s if r['pay'] > r['inv'])
        v = sum(base[r['plan']] * (1 - base[r['plan']]) for r in s)
        if v <= 0: continue
        conc = n / len({r['cup'] for r in s})
        D_.append(dict(d=d, n=n, o=o, e=e, v=v, conc=conc,
                       z=(o - e) / np.sqrt(v)))
    conc = np.array([x['conc'] for x in D_])
    q = np.percentile(conc, [33.3, 66.7])
    print(f"\n■ {nm}  {len(D_)}日  1節あたり商品数の三分位 {q[0]:.2f} / {q[1]:.2f}")
    print(f"  {'帯':16s} {'日数':>5s} {'件/日':>6s} {'実測%':>7s} {'構成期待%':>9s} "
          f"{'差pt':>7s} {'95%CI(差)':>20s}")
    for lab, m in (("薄い（節が多い）", conc <= q[0]),
                   ("中",              (conc > q[0]) & (conc <= q[1])),
                   ("濃い（節が少ない）", conc > q[1])):
        sub = [x for x, k in zip(D_, m) if k]
        O = sum(x['o'] for x in sub); E = sum(x['e'] for x in sub)
        N = sum(x['n'] for x in sub)
        bs = []
        for _ in range(2000):
            p = rng.integers(0, len(sub), len(sub))
            oo = sum(sub[i]['o'] for i in p); ee = sum(sub[i]['e'] for i in p)
            nn = sum(sub[i]['n'] for i in p)
            bs.append((oo - ee) / nn * 100)
        lo, hi = np.percentile(bs, [2.5, 97.5])
        print(f"  {lab:16s} {len(sub):5d} {N/len(sub):6.2f} {O/N*100:7.2f} "
              f"{E/N*100:9.2f} {(O-E)/N*100:+7.2f} [{lo:+.2f},{hi:+.2f}]")
    # 件数を揃えたときに残るか（件数の三分位内で濃淡を比べる）
    nn = np.array([x['n'] for x in D_]); qn = np.percentile(nn, [50])
    print("  件数の中央値で分けた中でも残るか:")
    for lab, m in (("件数 少", nn <= qn[0]), ("件数 多", nn > qn[0])):
        sub = [x for x, k in zip(D_, m) if k]
        c = np.array([x['conc'] for x in sub]); med = np.median(c)
        for l2, m2 in (("薄い", c <= med), ("濃い", c > med)):
            s2 = [x for x, k in zip(sub, m2) if k]
            O = sum(x['o'] for x in s2); E = sum(x['e'] for x in s2)
            N = sum(x['n'] for x in s2)
            print(f"    {lab} × {l2:4s} {len(s2):4d}日 差 {(O-E)/N*100:+6.2f}pt")
