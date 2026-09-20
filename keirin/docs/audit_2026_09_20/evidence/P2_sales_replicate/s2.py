from common import *
d = load()
tl = pd.read_csv("../G_sales/tl.csv", dtype={"race_key":str})
tl = tl.rename(columns={"plan_key":"rank_key"})
j = d.merge(tl[["race_key","rank_key","type_label","pred_mean_payout","pred_min_payout","n_legs","axis_sum","gap","tl_race_type"]],
            on=["race_key","rank_key"], how="left", suffixes=("","_tl"))
print("merged pred_mean_payout notna:", j["pred_mean_payout"].notna().sum(), "/", len(j))
s = j[j["pred_mean_payout"].notna() & j["plan_pay"].notna()]
r = s["pred_mean_payout"]/s["plan_pay"]
print(f"n={len(s)} ratio(tl/mine): median={r.median():.4f} p10={r.quantile(.1):.3f} p90={r.quantile(.9):.3f}")
print("  |log ratio|>0.1 share:", float((np.abs(np.log(r))>0.1).mean()))
print("  corr log:", np.corrcoef(np.log(s['pred_mean_payout']),np.log(s['plan_pay']))[0,1].round(4))
print("  n_legs mismatch share:", float((s["n_legs_tl"]!=s["n_legs"]).mean()))
j.to_pickle("ds.pkl")
# per-plan summary of the true planned payout (what the buyer's title implies)
tl_era = j[j["date"]>="20260829"]
g = tl_era.groupby("plan").agg(n=("paid","size"), pay_med=("plan_pay","median"),
    legs=("n_legs","median"), paid_mean=("paid","mean"), paid_med=("paid","within" if False else "median"),
    sold=("paid", lambda x:(x>0).mean()), free=("free","mean"))
g["paid_share"]=tl_era.groupby("plan").apply(lambda t: t["paid"].sum()/max(t["paid"].sum()+t["free"].sum(),1))
print("\n=== plan別（型ラボ期 08/29-） ===")
print(g.sort_values("paid_mean",ascending=False).round(3).to_string())
