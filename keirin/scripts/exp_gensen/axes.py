"""厳選AI(614)の軸・相手の選び方を自社指標と突き合わせる。"""
from __future__ import annotations
import json, sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

R = [json.loads(l) for l in Path("gensen/joined.jsonl").open(encoding="utf-8")]
if len(sys.argv) >= 3:
    lo, hi = sys.argv[1], sys.argv[2]
    R = [r for r in R if lo <= r["date"] <= hi]
print(f"joined {len(R)}  {min(r['date'] for r in R)}〜{max(r['date'] for r in R)}")

def i(d): return {int(k): v for k, v in d.items()}

def tri_legs(r):
    """3連単の (1着列,2着列,3着列) をまとめる。全 leg が同じ軸なら1組。"""
    out = []
    for l in r["legs"]:
        if l["bet_type"] != "3連単":
            continue
        out.append(l["cols"])
    return out

def axis(r):
    """全 leg の1着列/2着列が単一車なら (a1,a2,3着集合) を返す。"""
    cols = tri_legs(r)
    if not cols: return None
    a1 = {c[0][0] for c in cols if c and c[0]}
    a2 = {c[1][0] for c in cols if len(c) > 1 and len(c[1]) == 1}
    if len(a1) != 1: return None
    third = set()
    for c in cols:
        if len(c) > 2: third |= set(c[2])
    if len(a2) != 1: return (a1.pop(), None, third)
    return (a1.pop(), a2.pop(), third)

def fin(r):
    return {int(rk): fn for rk, fn in r["result"] if str(rk).isdigit()}

ok = [r for r in R if axis(r) and len(r["result"]) >= 3]
print("軸抽出できた:", len(ok))
form = Counter()
for r in ok:
    a1, a2, th = axis(r)
    form[("固定2軸" if a2 else "1着のみ固定")] += 1
print("形:", dict(form))

# ---- 軸1/軸2 は誰か ----
def ranks_of(r, fn):
    return dict(
        p3=i(r["p3_rank"]).get(fn), pw=i(r["pw_rank"]).get(fn),
        mkt=i(r["mkt_rank"]).get(fn), pt=i(r["pt_rank"]).get(fn))

cnt = defaultdict(Counter)
for r in ok:
    a1, a2, th = axis(r)
    for key, v in ranks_of(r, a1).items(): cnt["a1_"+key][v] += 1
    if a2:
        for key, v in ranks_of(r, a2).items(): cnt["a2_"+key][v] += 1
for k in ("a1_pw","a1_p3","a1_mkt","a1_pt","a2_pw","a2_p3","a2_mkt","a2_pt"):
    c = cnt[k]; n = sum(c.values())
    top = " ".join(f"{r}位:{c[r]/n*100:.0f}%" for r in sorted(x for x in c if x is not None) if c[r]/n >= 0.03)
    print(f"  {k:7s} n={n} {top}")

# ---- ライン ----
lp = Counter(); leader = Counter()
for r in ok:
    a1, a2, th = axis(r)
    e = i(r["entries"])
    if a1 in e:
        lp[e[a1].get("line_pos")] += 1
        leader[bool(e[a1].get("is_line_leader"))] += 1
print("  軸1 line_pos:", dict(sorted(lp.items(), key=lambda kv: str(kv[0]))), " ライン先頭:", dict(leader))
same = Counter()
for r in ok:
    a1, a2, th = axis(r)
    if not a2: continue
    e = i(r["entries"])
    if a1 in e and a2 in e:
        same[e[a1].get("line_group") == e[a2].get("line_group")] += 1
print("  軸1軸2 同ライン:", dict(same))

# ---- 当たり方 ----
n1 = n2 = both = 0
for r in ok:
    a1, a2, th = axis(r)
    f = fin(r)
    if f.get(1) == a1: n1 += 1
    if a2 and f.get(2) == a2: n2 += 1
    if a2 and f.get(1) == a1 and f.get(2) == a2: both += 1
na = sum(1 for r in ok if axis(r)[1])
print(f"  軸1が1着 {n1}/{len(ok)} = {n1/len(ok)*100:.1f}%   軸2が2着 {n2}/{na} = {n2/na*100:.1f}%"
      f"   二軸そろい {both/na*100:.1f}%")

# 参考: 自社 pw1位が1着になる率（同じ母集団）
for nm, key in (("pw1位", "pw_rank"), ("p3-1位", "p3_rank"), ("市場1番人気", "mkt_rank")):
    c = 0
    for r in ok:
        rk = i(r[key]); top = [f for f, v in rk.items() if v == 1]
        if top and fin(r).get(1) == top[0]: c += 1
    print(f"  参考 {nm} が1着: {c/len(ok)*100:.1f}%")

# ---- 3着列 ----
tc = Counter(len(axis(r)[2]) for r in ok)
print("  3着列の車数:", dict(sorted(tc.items())))
tr = Counter()
for r in ok:
    a1, a2, th = axis(r)
    rk = i(r["p3_rank"])
    for c in th: tr[rk.get(c)] += 1
n = sum(tr.values())
print("  3着に選ばれた車の自社p3順位:", " ".join(f"{k}位:{tr[k]/n*100:.0f}%" for k in sorted(x for x in tr if x is not None)))

# ---- レース選別 ----
print("\n[レース選別]")
print("  race_type:", dict(Counter(r["meta"]["race_type"] for r in R).most_common(10)))
print("  n_entries:", dict(Counter(r["meta"]["n_entries"] for r in R)))
print("  grade:", dict(Counter(r["meta"]["grade"] for r in R).most_common(8)))
