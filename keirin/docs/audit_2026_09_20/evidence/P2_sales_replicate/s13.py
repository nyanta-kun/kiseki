from common import *
g=pd.read_pickle("daily.pkl").copy()
g["ln"]=np.log(g["paid"]); g["lnn"]=np.log(g["n"]); g["t"]=np.arange(len(g))
g["share_hp"]=g["nhp"]/g["n"]; g["share_fin"]=g["nfinal"]/g["n"]; g["era"]=(g.index>="20260829").astype(float)
G=g.dropna(subset=["big10_lag"]).copy()
base=["lnn","share_fin","share_hp","t","era"]
def coef(df,cols,key,y="ln"):
    X,names=dmat(df,cols,None); res,_,_=ols(df[y].values,X,names); return res[key]
obs=coef(G,base+["big10_lag"],"big10_lag")[0]
print(f"### G4 置換検定（big10 の日付だけを無作為に入れ替える・4000回）")
rng=np.random.default_rng(7); vals=G["big10_lag"].values.copy(); null=[]
for _ in range(4000):
    p=rng.permutation(vals); s=G.copy(); s["big10_lag"]=p
    null.append(coef(s,base+["big10_lag"],"big10_lag")[0])
null=np.array(null)
print(f"  観測 {obs:+.3f}  帰無分布 中央 {np.median(null):+.3f} 95%上側 {np.percentile(null,97.5):+.3f}  片側p = {(null>=obs).mean():.4f}")
print("\n### G5 検出力: 表示的中率（前日/3日/7日）の係数と、検出可能な最小効果")
g["disp3"]=g["disp_hit"].rolling(3).mean().shift(1); g["disp7"]=g["disp_hit"].rolling(7).mean().shift(1)
g["big3"]=g["big10"].rolling(3).sum().shift(1); g["big7"]=g["big10"].rolling(7).sum().shift(1)
g["roi3"]=g["roi"].rolling(3).mean().shift(1)
for k in ["disp_lag","disp3","disp7","roi_lag","roi3","big3","big7"]:
    s=g.dropna(subset=[k,"big10_lag"])
    v=coef(s,base+[k],k)
    unit = "+10pt" if k.startswith("disp") else ("+0.1" if k.startswith("roi") else "+1件")
    mul = 0.10 if k.startswith("disp") else (0.1 if k.startswith("roi") else 1.0)
    print(f"  {k:<10} n={len(s)} {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]  → {unit} で売上 "
          f"×{np.exp(v[0]*mul):.3f} [×{np.exp(v[1]*mul):.3f}, ×{np.exp(v[2]*mul):.3f}]")
print("\n### G6 本数の弾力性")
for y,lab in [("ln","log(日有償pt)")]:
    for cols,l2 in [(["lnn","t","era"],"素"),(base,"+構成"),(base+["big10_lag"],"+構成+前日10万")]:
        s=G.dropna(subset=cols); v=coef(s,cols,"lnn",y)
        print(f"  {l2:<16} lnn {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
g["lper"]=np.log(g["paid"]/g["n"])
v=coef(G,base,"lnn","lper"); print(f"  1商品あたり      lnn {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
print("\n### 収益（円）")
R,REF=0.30,0.3
tot=g["paid"].sum(); print(f"  49日 有償pt {tot:,.0f} → 売上 {tot*R:,.0f}円 → 手取り {tot*R*(1-REF):,.0f}円 = {tot*R*(1-REF)/49:,.0f}円/日")
tl=g[g.index>="20260829"]; print(f"  型ラボ期21日: {tl['paid'].mean():,.0f}pt/日 → 手取り {tl['paid'].mean()*R*(1-REF):,.0f}円/日 = 月 {tl['paid'].mean()*R*(1-REF)*30:,.0f}円")
print(f"  高額枠1本の限界（10枠期の6本目以降 774pt）→ 手取り {774*R*(1-REF):,.0f}円/本")
print(f"  高額枠1本の平均（5枠期 1,493pt）      → 手取り {1493*R*(1-REF):,.0f}円/本")
print(f"  自信あり1件の上乗せ（+1,804pt・G3）    → 手取り {1804*R*(1-REF):,.0f}円/件")
