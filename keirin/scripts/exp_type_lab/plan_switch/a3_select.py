"""Part2: プランは現行のまま、日次上限（判定の半分）で残すレースの選び方を替える。"""
import random
from collections import defaultdict
from common import *
rows = load()

def run(R, score, frac=0.5):
    byday = defaultdict(list)
    for r in R:
        b = baseline(r)
        if b: byday[r["date"]].append((r, b))
    out = []
    for day, c in byday.items():
        judged = [x for x in c if not G.daily_cap_exempt(x[0]["rtype"], x[0]["cupg"]) and not G.daily_cap_exempt_plan(x[1])]
        budget = max(1, int(len(judged) * frac)) if judged else 0
        n = 0
        for r, b in sorted(c, key=lambda x: -score(*x)):
            ex = G.daily_cap_exempt(r["rtype"], r["cupg"]) or G.daily_cap_exempt_plan(b)
            if not ex:
                if n >= budget: continue
                n += 1
            v = r["plans"][b]; out.append((day, b, v["inv"], v["pay"], r))
    return out

def pct_rank(R, f):
    """プラン内順位（探索窓の分布で）。"""
    d = defaultdict(list)
    for r in rows:
        b = baseline(r)
        if r["win"] == "exp" and b: d[b].append(f(r, b))
    d = {k: np.sort(v) for k, v in d.items()}
    return lambda r, b: np.searchsorted(d[b], f(r, b)) / max(1, len(d[b]))

sp = lambda r, b: r["plans"][b]["sp"]
ev = lambda r, b: r["plans"][b]["sp"] * r["plans"][b]["mean"] / r["plans"][b]["inv"]
scores = {
    "現行 cap_priority": lambda r, b: G.cap_priority(b, r["axis_sum"], r["rp_sd"]),
    "axis_sum のみ(プラン内順位)": pct_rank(None, lambda r, b: r["axis_sum"]),
    "Σp(プラン内順位)": pct_rank(None, sp),
    "Σp(絶対値)": sp,
    "モデル期待回収 Σp×計画/投資": ev,
    "モデル期待回収(プラン内順位)": pct_rank(None, ev),
}
for w in ("exp", "conf"):
    R = [r for r in rows if r["win"] == w]
    days = {r["date"] for r in R}; nd = len(days)
    print(f"\n== {w}"); print(HEAD + "   Δ表示[CI]        ΔROI[CI]")
    base = run(R, scores["現行 cap_priority"])
    for name, s in scores.items():
        rs = run(R, s)
        ci = paired(base, rs, days)
        print(fmt(name, summ(rs, nd)) + ("" if name.startswith("現行") else f"  [{ci[0][0]:+.1f},{ci[0][1]:+.1f}] [{ci[1][0]:+.1f},{ci[1][1]:+.1f}]"))
    rr = [summ(run(R, lambda r, b, g=random.Random(sd): g.random()), nd) for sd in range(20)]
    print(f"{'無作為(20本中央)':<30}{'':>6}{np.median([s['shown'] for s in rr]):>7.2f}{np.median([s['roi'] for s in rr]):>7.1f}{np.median([s['roi3'] for s in rr]):>7.1f}{np.median([s['roi10'] for s in rr]):>7.1f}   現行に勝つ本数 表示{sum(s['shown']<summ(base,nd)['shown'] for s in rr)}/20 ROI{sum(s['roi']<summ(base,nd)['roi'] for s in rr)}/20")
    print(fmt("上限なし(全部売る)", summ(recs(R, baseline), nd)))
