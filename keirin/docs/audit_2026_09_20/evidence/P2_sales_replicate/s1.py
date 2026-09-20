from common import *
d = load()
print("rows", len(d), "days", d["date"].nunique())
print("matched submission:", d["rank_key"].notna().sum())
print("plan_pay notna:", d["plan_pay"].notna().sum())
print("\n-- sanity: avg_sold_points unique:", d["avg_sold_points"].unique()[:5])
print("sold_points == n_sold*300 exceptions:", int(((d["sold_points"]-d["n_sold"]*300).abs()>0).sum()))
print("\n-- paid share overall:", d["paid"].sum()/d["sold_points"].sum())
# G used pred_mean_payout from type_lab_picks joined DISTINCT ON race_key.
g = pd.read_csv("../G_sales/races.csv", dtype={"race_key":str,"race_id":str})
m = g[["race_id","rank_key","plan_key","pred_mean_payout","n_legs"]].rename(columns={"n_legs":"g_nlegs"})
j = d.merge(m, on="race_id", how="left", suffixes=("","_g"))
ok = j["plan_key"].notna() & j["rank_key"].notna()
print("\n[G join check] rows with both:", int(ok.sum()))
print("  G's tl.plan_key == actual submitted rank_key:", float((j.loc[ok,"plan_key"]==j.loc[ok,"rank_key"]).mean()))
mism = j.loc[ok & (j["plan_key"]!=j["rank_key"])]
print("  mismatched n =", len(mism))
print(mism.groupby(["rank_key","plan_key"]).size().sort_values(ascending=False).head(12))
# how different is the planned payout when mismatched
sub = j[ok & j["pred_mean_payout"].notna() & j["plan_pay"].notna()]
import numpy as np
same = sub[sub["plan_key"]==sub["rank_key"]]; diff = sub[sub["plan_key"]!=sub["rank_key"]]
for nm,s in [("same",same),("mismatch",diff)]:
    if len(s):
        rr = s["pred_mean_payout"]/s["plan_pay"]
        print(f"  {nm}: n={len(s)} median ratio pred_mean_payout/mine = {rr.median():.3f}  corr(log)={np.corrcoef(np.log(s['pred_mean_payout']),np.log(s['plan_pay']))[0,1]:.3f}")
