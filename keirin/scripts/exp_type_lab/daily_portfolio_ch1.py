"""§1 日次的中率の分散分解（過分散の検定）。"""
import sys; sys.path.insert(0,'scripts/exp_type_lab')
import numpy as np
from collections import defaultdict
import daily_portfolio as D

rng = np.random.default_rng(20260910)

def fit_p(rows, keys):
    """leave-one-day-out の期待的中確率。keys=グループ化関数のリスト（階層）。"""
    # 全体・各階層のセル平均（LOO: その日を除いて推定）
    cell = defaultdict(lambda: [0, 0])       # key -> [n, hits]
    byday = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    tot = [0, 0]
    todayagg = defaultdict(lambda: [0, 0])
    for r in rows:
        k = keys(r)
        cell[k][0] += 1; cell[k][1] += r["shown"]
        byday[r["date"]][k][0] += 1; byday[r["date"]][k][1] += r["shown"]
        tot[0] += 1; tot[1] += r["shown"]
        todayagg[r["date"]][0] += 1; todayagg[r["date"]][1] += r["shown"]
    out = []
    for r in rows:
        k = keys(r); d = r["date"]
        n = cell[k][0] - byday[d][k][0]; h = cell[k][1] - byday[d][k][1]
        gn = tot[0] - todayagg[d][0]; gh = tot[1] - todayagg[d][1]
        g = gh / gn
        # セルが薄いときは全体平均へ縮小（擬似カウント20）
        p = (h + 20 * g) / (n + 20)
        out.append(min(max(p, 1e-6), 1 - 1e-6))
    return np.array(out)

def dispersion(rows, p):
    d = defaultdict(lambda: [0.0, 0.0, 0.0])   # date -> [O, E, V]
    for r, pi in zip(rows, p):
        a = d[r["date"]]
        a[0] += r["shown"]; a[1] += pi; a[2] += pi * (1 - pi)
    O = np.array([a[0] for a in d.values()]); E = np.array([a[1] for a in d.values()])
    V = np.array([a[2] for a in d.values()])
    chi2 = float(((O - E) ** 2 / V).sum()); df = len(O)
    return chi2 / df, O, E, V

def boot_null(rows, p, B=2000):
    """独立ベルヌーイの帰無分布（同じ p・同じ日ごとの件数）。"""
    idx = defaultdict(list)
    for j, r in enumerate(rows):
        idx[r["date"]].append(j)
    days = list(idx)
    out = np.empty(B)
    P = p
    for b in range(B):
        h = (rng.random(len(P)) < P).astype(float)
        s = 0.0
        for dd in days:
            jj = idx[dd]
            O = h[jj].sum(); E = P[jj].sum(); V = (P[jj] * (1 - P[jj])).sum()
            s += (O - E) ** 2 / V
        out[b] = s / len(days)
    return out

MODELS = [
    ("① 全体平均のみ", lambda r: "*"),
    ("② プラン(=型×狙い)", lambda r: r["plan"]),
    ("③ ②+開催日目", lambda r: (r["plan"], min(int(r["dayidx"]), 6))),
    ("④ ③+時間帯(波)", lambda r: (r["plan"], min(int(r["dayidx"]), 6), r["wave"])),
    ("⑤ ④+会場", lambda r: (r["plan"], min(int(r["dayidx"]), 6), r["wave"], r["venue"])),
]

if __name__ == "__main__":
 for nm, rows in (("探索 2024-07〜2025-12", D.EX), ("確認 2026-01〜08", D.CF)):
     sold = D.produce(rows)
     print("\n" + "=" * 96)
     print(f"■ {nm}  実際に出る商品 {len(sold):,}件 / {len(set(r['date'] for r in sold))}日")
     print("=" * 96)
     print(f"{'説明モデル':28s} {'φ=χ²/df':>9s} {'帰無95%上限':>11s} {'p値':>7s} {'説明できた過分散':>16s}")
     base = None
     for mn, kf in MODELS:
         p = fit_p(sold, kf)
         phi, O, E, V = dispersion(sold, p)
         null = boot_null(sold, p, 1000)
         hi = float(np.percentile(null, 95)); pv = float((null >= phi).mean())
         if base is None:
             base = phi
         print(f"{mn:28s} {phi:9.3f} {hi:11.3f} {pv:7.3f} "
               f"{'—' if base==phi else f'{(base-phi)/(base-1)*100:5.1f}%' if base>1 else '—':>16s}")
     # 日次の実測ばらつき
     dd = D.daily(sold)
     sh = np.array([a[1]/a[0]*100 for a in dd.values()])
     n = np.array([a[0] for a in dd.values()])
     print(f"\n  日次表示的中: 平均{sh.mean():.2f}% SD{sh.std(ddof=1):.2f}pt "
           f"（件数中央{np.median(n):.0f}件・二項SD≈{np.sqrt(sh.mean()/100*(1-sh.mean()/100)/np.median(n))*100:.2f}pt）")
 