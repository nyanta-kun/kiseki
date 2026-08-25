import numpy as np, pandas as pd
D = pd.read_pickle("/tmp/diag9.pkl")
rng=np.random.default_rng(7)
def prep(nc, d=None):
    d = D[D.nc==nc] if d is None else d
    a1=d[d.p3rank==1].set_index("rk"); a2=d[d.p3rank==2].set_index("rk")
    j=pd.DataFrame({"a1":a1.top3,"a2":a2.top3,"lg1":a1.lg,"lg2":a2.lg,"date":a1.date,"rtype":a1.rtype})
    j["same"]=(j.lg1.notna()&(j.lg1==j.lg2)); return j
print("=== 依存の大きさ（実測二軸そろい − 独立仮定）年別 ===")
for nc in (9,7):
    j=prep(nc); j["yr"]=j.date.str[:4]
    for yr,g in j.groupby("yr"):
        for lab,s in (("同",g[g.same]),("別",g[~g.same])):
            if len(s)<150: continue
            obs=(s.a1*s.a2).mean(); ind=s.a1.mean()*s.a2.mean()
            d=(s.a1*s.a2).values-0  # bootstrap on obs only
            b=[( (s.a1.values[i]*s.a2.values[i]).mean() - s.a1.values[i].mean()*s.a2.values[i].mean() )
               for i in (rng.integers(0,len(s),len(s)) for _ in range(2000))]
            print(f"  {nc}車 {yr} {lab}ライン n={len(s):5d}  実測 {obs*100:5.2f}%  独立 {ind*100:5.2f}%  "
                  f"依存 {100*(obs-ind):+5.2f}pt CI[{np.percentile(b,2.5)*100:+.2f},{np.percentile(b,97.5)*100:+.2f}]")
    print()
