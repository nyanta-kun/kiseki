"""H03 補足: 新規決勝の プラン別ROI と 上位3件除外ROIの日ブートストラップCI（h03_result.pkl から）。"""
import pickle, numpy as np, collections
from pathlib import Path
D = Path(__file__).resolve().parents[2] / "data/exp_bet_review"
r = pickle.load(open(D / "h03_result.pkl", "rb"))
by2 = {x["race_key"]: x for x in r["rec2"]}
nr = [by2[k] for k in r["new_keys"]]
g = collections.defaultdict(list)
for x in nr: g[x["plan"]].append(x)
for p, v in sorted(g.items(), key=lambda t: -len(t[1])):
    inv = sum(x["inv"] for x in v); pay = sum(x["pay"] for x in v)
    print(f"{p}: R={len(v)} ROI={pay/inv*100:.1f}% 的中={sum(x['pay']>0 for x in v)} 払戻最大={max(x['pay'] for x in v):,.0f}")
days = sorted({x["day"] for x in nr}); pos = {d: i for i, d in enumerate(days)}
rng = np.random.default_rng(7)
def top3(sample_days):
    cnt = collections.Counter(sample_days)
    pays = []; inv = 0
    for d, c in cnt.items():
        for x in byday[d]:
            pays += [x["pay"]] * c; inv += x["inv"] * c
    pays.sort(reverse=True)
    return (sum(pays) - sum(pays[:3])) / inv * 100, sum(pays) / inv * 100
byday = collections.defaultdict(list)
for x in nr: byday[x["day"]].append(x)
res = np.array([top3(list(rng.choice(days, len(days)))) for _ in range(2000)])
print("新規 ROI 上位3件除外 点推定", top3(days)[0], "CI", np.percentile(res[:, 0], [2.5, 97.5]), "P(<65)=%.3f" % (res[:, 0] < 65).mean())
print("新規 ROI(全) CI", np.percentile(res[:, 1], [2.5, 97.5]))
# 全決勝(②) の上位3件除外 vs 非劣性比較水準
f2 = [x for x in r["rec2"] if x["race_key"] in r["fset"]]
for lab, sel in (("F_sign新規", [x for x in nr if x["plan"] == "F_sign"]), ("F以外新規", [x for x in nr if x["plan"] != "F_sign"])):
    pays = sorted((x["pay"] for x in sel), reverse=True); inv = sum(x["inv"] for x in sel)
    print(lab, len(sel), f"ROI {sum(pays)/inv*100:.1f}% top3除外 {(sum(pays)-sum(pays[:3]))/inv*100:.1f}%")
