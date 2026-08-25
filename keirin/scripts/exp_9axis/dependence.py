import numpy as np, pandas as pd
D = pd.read_pickle("/tmp/diag9.pkl")

def prep(nc):
    d = D[D.nc==nc]
    a1 = d[d.p3rank==1].set_index("rk")
    a2 = d[d.p3rank==2].set_index("rk")
    j = pd.DataFrame({
        "a1_top3": a1.top3, "a2_top3": a2.top3,
        "a1_lg": a1.lg, "a2_lg": a2.lg,
        "a1_lpos": a1.lpos, "a2_lpos": a2.lpos,
        "a1_lead": a1.leader, "a2_lead": a2.leader,
        "a1_p3": a1.p3, "a2_p3": a2.p3, "rtype": a1.rtype})
    j["same"] = (j.a1_lg.notna() & (j.a1_lg == j.a2_lg))
    return d, j

for nc in (9,7):
    d, j = prep(nc)
    print(f"########## {nc}車  n={len(j):,}R ##########")
    print(f"  軸1 3着内 {j.a1_top3.mean()*100:5.2f}% / 軸2 3着内 {j.a2_top3.mean()*100:5.2f}%")
    print(f"  P(軸23着内 | 軸1 3着内)   {j[j.a1_top3==1].a2_top3.mean()*100:5.2f}%")
    print(f"  P(軸23着内 | 軸1 着外)    {j[j.a1_top3==0].a2_top3.mean()*100:5.2f}%")
    print(f"  二軸そろい 実測 {(j.a1_top3*j.a2_top3).mean()*100:5.2f}%"
          f"  / 独立仮定 {(j.a1_top3.mean()*j.a2_top3.mean())*100:5.2f}%"
          f"  / モデル積 {(j.a1_p3*j.a2_p3).mean()*100:5.2f}%")
    print("  -- 同ライン / 別ライン --")
    for lab, s in (("同ライン", j[j.same]), ("別ライン", j[~j.same])):
        if not len(s): continue
        print(f"    {lab} {len(s):5d}R ({len(s)/len(j)*100:4.1f}%) 二軸そろい {(s.a1_top3*s.a2_top3).mean()*100:5.2f}%"
              f"  独立仮定 {(s.a1_top3.mean()*s.a2_top3.mean())*100:5.2f}%"
              f"  条件付 P(a2|a1) {s[s.a1_top3==1].a2_top3.mean()*100:5.2f}%")
    print()

print("########## 9車: 軸1 との関係別に見た「軸1が3着内のときの候補の3着内率」 ##########")
d, j = prep(9)
d = d.copy()
a1 = d[d.p3rank==1].set_index("rk")
d["a1_lg"] = d.rk.map(a1.lg); d["a1_lpos"] = d.rk.map(a1.lpos)
d["a1_top3"] = d.rk.map(a1.top3); d["a1_f"] = d.rk.map(a1.f)
o = d[(d.f != d.a1_f) & (d.a1_top3==1)].copy()
def rel(r):
    if pd.isna(r.lg) or pd.isna(r.a1_lg): return "単騎/不明"
    if r.lg != r.a1_lg: return "別ライン" + ("先頭" if r.leader==1 else "")
    dp = r.lpos - r.a1_lpos
    return {1:"同ライン 直後", -1:"同ライン 直前", 2:"同ライン +2"}.get(dp, "同ライン その他")
o["rel"] = o.apply(rel, axis=1)
t = o.groupby("rel").agg(n=("top3","size"), pred_p3=("p3","mean"), act=("top3","mean"),
                         p3rank=("p3rank","mean"))
t["残差pt"] = 100*(t.act - t.pred_p3)
print(t[t.n>=200].sort_values("act", ascending=False).round(4))
