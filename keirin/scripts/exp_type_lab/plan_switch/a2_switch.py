"""Part1: 売るレースは現行のまま、適用プランだけ切り替える。"""
import random
from common import *
rows = load()
CORE = ["A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "F_sign", "A_ana", "A_trio"]
HIT = ["A_hit", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit", "A_trio"]
RG = lambda r: "final" if "決勝" in r["rtype"] else ("semi" if "準決勝" in r["rtype"] else "other")

# axis tercile within type (explore)
qs = {}
for t in "ABCDEF":
    a = [r["axis_sum"] for r in rows if r["win"] == "exp" and r["type"] == t]
    qs[t] = np.quantile(a, [1/3, 2/3])
def cell(r):
    q = qs[r["type"]]
    return (r["type"], int(r["axis_sum"] > q[0]) + int(r["axis_sum"] > q[1]), RG(r))

def avail(r, ks):  # gate-passing (入稿ゲートのみ・軸信頼は現行行で通過済み)
    return [k for k in ks if ok(r, k, axis_gate=False)]

def mk(choose):
    def pol(r):
        b = baseline(r)
        if not b:
            return None
        k = choose(r, b)
        return k if k and ok(r, k, axis_gate=False) else b
    return pol

# cell-best learned on explore
def learn(metric, cands, minn=150):
    tab = {}
    for r in rows:
        if r["win"] != "exp" or not baseline(r):
            continue
        for k in cands:
            if ok(r, k, axis_gate=False):
                v = r["plans"][k]; tab.setdefault(cell(r), {}).setdefault(k, []).append((v["inv"], v["pay"]))
    best = {}
    for c, d in tab.items():
        sc = {}
        for k, l in d.items():
            if len(l) < minn: continue
            inv = np.array([x[0] for x in l]); pay = np.array([x[1] for x in l])
            if metric == "roi10":
                ps = np.sort(pay)[::-1]; sc[k] = (pay.sum() - ps[:10].sum()) / inv.sum()
            elif metric == "shown":
                sc[k] = np.mean(pay > inv)
        if sc: best[c] = max(sc, key=sc.get)
    return best

B_roi = learn("roi10", CORE)
B_shown = learn("shown", HIT)
pols = {
    "現行": baseline,
    "セル最良(探索ROI除10)": mk(lambda r, b: B_roi.get(cell(r), b)),
    "セル最良(探索表示的中)": mk(lambda r, b: B_shown.get(cell(r), b)),
    "Σp最大(的中系)": mk(lambda r, b: max(avail(r, HIT) or [b], key=lambda k: r["plans"][k]["sp"])),
    "Σp×計画払戻 最大": mk(lambda r, b: max(avail(r, CORE) or [b], key=lambda k: r["plans"][k]["sp"] * r["plans"][k]["mean"])),
    "Σp最大(計画払戻≥現行)": mk(lambda r, b: max([k for k in avail(r, HIT) if r["plans"][k]["mean"] >= r["plans"][b]["mean"] - 1] or [b], key=lambda k: r["plans"][k]["sp"])),
    "型F非看板→E_hit": mk(lambda r, b: "E_hit" if b == "F_hit" else b),
    "型A→B_hit": mk(lambda r, b: "B_hit" if b == "A_hit" else b),
}
def oracle(r):
    b = baseline(r)
    if not b: return None
    a = avail(r, CORE) or [b]
    return max(a, key=lambda k: r["plans"][k]["pay"] - r["plans"][k]["inv"])
for w in ("exp", "conf"):
    R = [r for r in rows if r["win"] == w]
    days = {r["date"] for r in R}; nd = len(days)
    print(f"\n== {w} ({nd}日)"); print(HEAD + "   Δ表示[CI]        ΔROI[CI]")
    base = recs(R, baseline)
    for name, p in pols.items():
        rs = recs(R, p)
        sw = np.mean([x[1] != y[1] for x, y in zip(rs, base)]) * 100
        if name == "現行":
            print(fmt(name, summ(rs, nd))); continue
        ci = paired(base, rs, days)
        print(fmt(name, summ(rs, nd)) + f"  [{ci[0][0]:+.1f},{ci[0][1]:+.1f}] [{ci[1][0]:+.1f},{ci[1][1]:+.1f}] 替{sw:.0f}%")
    # random control
    rr = []
    for sd in range(20):
        rng = random.Random(sd)
        rs = recs(R, mk(lambda r, b: rng.choice(avail(r, CORE) or [b])))
        rr.append(summ(rs, nd))
    print(f"{'無作為に替える(20本の中央)':<30}" + f"{'':>6}{np.median([s['shown'] for s in rr]):>7.2f}{np.median([s['roi'] for s in rr]):>7.1f}{np.median([s['roi3'] for s in rr]):>7.1f}{np.median([s['roi10'] for s in rr]):>7.1f}")
    print(fmt("(参考)後知恵の最良", summ(recs(R, oracle), nd)))
print("\nセル最良ROI:", {k: v for k, v in sorted(B_roi.items())})
