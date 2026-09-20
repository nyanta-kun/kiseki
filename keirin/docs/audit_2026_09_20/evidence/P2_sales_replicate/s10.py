from common import *
g=pd.read_pickle("daily.pkl").copy()
g["ln"]=np.log(g["paid"]); g["lnn"]=np.log(g["n"])
g["share_hp"]=g["nhp"]/g["n"]; g["share_fin"]=g["nfinal"]/g["n"]
g["era"]=(g.index>="20260829").astype(float)
G=g.dropna(subset=["big10_lag"]).copy()
def fit(df, cols, y="ln"):
    X,names=dmat(df,cols,None); res,b,se=ols(df[y].values,X,names)
    return res,len(df)
base=["lnn","share_fin","share_hp","t","era"]
print("### G4 再現と頑健性: y=log(日有償pt)")
for lab,cols in [("G仕様", base+["big10_lag"]),
                 ("+ 曜日ダミー", base+["big10_lag"]),
                 ]:
    if "曜日" in lab:
        df=G.copy()
        for k in range(1,7): df[f"dw{k}"]=(df["dow"]==k).astype(float)
        cols2=cols+[f"dw{k}" for k in range(1,7)]
        res,n=fit(df,cols2); v=res["big10_lag"]
        print(f"  {lab:<14} n={n} big10_lag {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
    else:
        res,n=fit(G,cols); v=res["big10_lag"]
        print(f"  {lab:<14} n={n} big10_lag {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
res,n=fit(G, base+["big10_lag","paid_lag"]); print(f"  {'+前日売上':<14} n={n} big10_lag {res['big10_lag'][0]:+.3f} [{res['big10_lag'][1]:+.3f},{res['big10_lag'][2]:+.3f}]")
G2=G.copy(); G2["lpl"]=np.log(G2["paid_lag"])
res,n=fit(G2, base+["big10_lag","lpl"]); print(f"  {'+log前日売上':<14} n={n} big10_lag {res['big10_lag'][0]:+.3f} [{res['big10_lag'][1]:+.3f},{res['big10_lag'][2]:+.3f}]")

print("\n### leave-one-out（イベント日ごと）")
ev=sorted(G.index[G["big10_lag"]>0])
print("  翌日 =", ev, " 曜日 =", [int(G.loc[e,'dow']) for e in ev])
full,_=fit(G,base+["big10_lag"]); print(f"  全部 {full['big10_lag'][0]:+.3f}")
for e in ev:
    s=G.drop(index=e); r,_=fit(s,base+["big10_lag"])
    print(f"  −{e} (dow={int(G.loc[e,'dow'])}) → {r['big10_lag'][0]:+.3f} [{r['big10_lag'][1]:+.3f},{r['big10_lag'][2]:+.3f}]  (その日 paid={G.loc[e,'paid']:,.0f})")
print("\n### プラセボ: 翌日の big10 で当日を説明")
G3=G.copy(); G3["big10_lead"]=g["big10"].shift(-1).reindex(G3.index)
G3=G3.dropna(subset=["big10_lead"])
r,n=fit(G3,base+["big10_lead"]); print(f"  n={n} big10_lead {r['big10_lead'][0]:+.3f} [{r['big10_lead'][1]:+.3f},{r['big10_lead'][2]:+.3f}]")
print("\n### 曜日だけ（イベント無し）")
df=G.copy()
for k in range(1,7): df[f"dw{k}"]=(df["dow"]==k).astype(float)
r,n=fit(df, base+[f"dw{k}" for k in range(1,7)])
for k in range(1,7): v=r[f"dw{k}"]; print(f"  dow={k}: {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
