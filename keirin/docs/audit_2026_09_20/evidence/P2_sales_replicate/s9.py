from common import *
d = pd.read_pickle("ds.pkl")
dl = pd.read_csv("daily.csv", dtype={"sale_date":str})
dl["date"]=dl["sale_date"]
# per-race settled payouts from submissions (netkeirin側の払戻)
d["pay_out"]=d["payout_amount"].fillna(0).astype(float)
d["big10"]=(d["pay_out"]>=100000).astype(int)
g=d.groupby("date").agg(n=("paid","size"), paid=("paid","sum"), big10=("big10","sum"),
    maxpay=("pay_out","max"), hits=("n_hits_excl_garami","sum"),
    hits_g=("n_hits_incl_garami","sum"), stake=("stake_amount","sum"), payout=("pay_out","sum"),
    nhp=("origin",lambda x:(x=="highpay_fill").sum()),
    nfinal=("final","sum"))
g["disp_hit"]=g["hits"]/g["n"]; g["roi"]=g["payout"]/g["stake"]
g["dow"]=pd.to_datetime(g.index,format="%Y%m%d").dayofweek
g=g.sort_index()
g["big10_lag"]=g["big10"].shift(1); g["paid_lag"]=g["paid"].shift(1)
g["disp_lag"]=g["disp_hit"].shift(1); g["roi_lag"]=g["roi"].shift(1)
g["t"]=np.arange(len(g))
pd.set_option("display.width",250)
print(g[["n","paid","big10","maxpay","disp_hit","roi","nhp","nfinal","dow"]].round(3).to_string())
g.to_pickle("daily.pkl")
