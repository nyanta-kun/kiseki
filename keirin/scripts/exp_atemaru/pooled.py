"""両窓プールで『軸の寄与』と『相手の寄与』を分離し、レース単位 paired bootstrap で CI を出す。"""
import json, random
from collections import Counter
from pathlib import Path
random.seed(20260825)
ROOT = Path(__file__).resolve().parent / "atemaru"
R = [json.loads(l) for l in (ROOT / "joined.jsonl").open(encoding="utf-8")]
R = [r for r in R if len(r["marks"]) == 4 and r["result"]]
UNIT = {1: 1000, 2: 400, 3: 200}


def I(d): return {int(k): v for k, v in d.items()}


rows = []
for r in R:
    e = I(r["entries"]); m = r["marks"]
    ax = m.get("◎"); part = [m.get("○"), m.get("▲"), m.get("△")]
    if ax is None or any(p is None for p in part): continue
    if ax not in e or any(p not in e for p in part): continue
    f = {int(k): v for k, v in ((rk, fn) for rk, fn in r["result"]) if str(k).isdigit()}
    f = {int(rk): fn for rk, fn in r["result"] if rk.isdigit()}
    order = [f.get(k) for k in (1, 2, 3)]
    if None in order or len(set(order)) < 3: continue
    rows.append(dict(r=r, win=r["date"][:6], e=e, ax=ax, part=part, order=order,
                     p3=I(r["p3_rank"]), mkt=I(r["mkt_rank"])))
print("プール母集団:", len(rows), Counter(x["win"] for x in rows))


def pay(row, ax, part):
    o = row["order"]
    pos = next((k for k in (1, 2, 3) if o[k-1] == ax), 0)
    if pos == 0: return 0
    if not all(x in part for x in [x for k, x in enumerate(o, 1) if k != pos]): return 0
    od = row["r"]["tri_odds"].get("-".join(str(x) for x in o))
    return None if od is None else od * 100 * UNIT[pos] / 100


def top(row, key, k):
    return [f for f in sorted(row[key], key=row[key].get)][:k]


ARMS = {
    "A 軸=アテマル / 相手=アテマル": lambda row: (row["ax"], row["part"]),
    "D 軸=アテマル / 相手=自社p3": lambda row: (row["ax"], [f for f in sorted(row["p3"], key=row["p3"].get) if f != row["ax"]][:3]),
    "F 軸=自社p3   / 相手=アテマル": None,
    "B 軸=自社p3   / 相手=自社p3": lambda row: (top(row, "p3", 4)[0], top(row, "p3", 4)[1:]),
}


def fpick(row):
    a = top(row, "p3", 1)[0]
    if a in row["part"]:
        p = [x for x in row["part"] if x != a]
        p += [f for f in sorted(row["p3"], key=row["p3"].get) if f != a and f not in p][:1]
    else:
        p = row["part"]
    return a, p[:3]


ARMS["F 軸=自社p3   / 相手=アテマル"] = fpick
P = {k: [pay(row, *v(row)) for row in rows] for k, v in ARMS.items()}
val = [i for i in range(len(rows)) if all(P[k][i] is not None for k in ARMS)]
print(f"有効 {len(val)}")
for k in ARMS:
    h = sum(1 for i in val if P[k][i] > 0)
    n = sum(1 for i in val if P[k][i] > 9600)
    pv = sum(P[k][i] for i in val)
    print(f"  {k:26s} 的中 {100*h/len(val):5.2f}%  表示的中 {100*n/len(val):5.2f}%  回収率 {100*pv/(9600*len(val)):5.1f}%")

B = 4000
def boot(a, b, stat):
    d = []
    for _ in range(B):
        s = [random.choice(val) for _ in val]
        d.append(stat(b, s) - stat(a, s))
    d.sort()
    return sum(d)/B, d[int(.025*B)], d[int(.975*B)]


hit = lambda k, s: 100*sum(1 for i in s if P[k][i] > 0)/len(s)
roi = lambda k, s: 100*sum(P[k][i] for i in s)/(9600*len(s))
print("\n  【的中率の差】(pt)")
for lbl, a, b in [("相手だけ自社p3へ (A→D)", "A 軸=アテマル / 相手=アテマル", "D 軸=アテマル / 相手=自社p3"),
                  ("軸だけ自社p3へ  (A→F)", "A 軸=アテマル / 相手=アテマル", "F 軸=自社p3   / 相手=アテマル"),
                  ("両方自社へ      (A→B)", "A 軸=アテマル / 相手=アテマル", "B 軸=自社p3   / 相手=自社p3")]:
    m, lo, hi = boot(a, b, hit)
    print(f"    {lbl}: {m:+6.2f}pt  CI95 [{lo:+6.2f}, {hi:+6.2f}]")
print("  【回収率の差】(pt)")
for lbl, a, b in [("相手だけ自社p3へ (A→D)", "A 軸=アテマル / 相手=アテマル", "D 軸=アテマル / 相手=自社p3"),
                  ("軸だけ自社p3へ  (A→F)", "A 軸=アテマル / 相手=アテマル", "F 軸=自社p3   / 相手=アテマル"),
                  ("両方自社へ      (A→B)", "A 軸=アテマル / 相手=アテマル", "B 軸=自社p3   / 相手=自社p3")]:
    m, lo, hi = boot(a, b, roi)
    print(f"    {lbl}: {m:+6.2f}pt  CI95 [{lo:+6.2f}, {hi:+6.2f}]")

# 窓別
print("\n  窓別の的中率")
for w in sorted(set(x["win"] for x in rows)):
    idx = [i for i in val if rows[i]["win"] == w]
    line = f"    {w} n={len(idx):5d} "
    for k in ARMS:
        line += f" {k[0]}={100*sum(1 for i in idx if P[k][i]>0)/len(idx):5.2f}%"
    print(line)
