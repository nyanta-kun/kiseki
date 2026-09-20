from common import *
g=pd.read_pickle("daily.pkl").copy()
g["ln"]=np.log(g["paid"]); g["lnn"]=np.log(g["n"]); g["t"]=np.arange(len(g))
g["share_hp"]=g["nhp"]/g["n"]; g["share_fin"]=g["nfinal"]/g["n"]; g["era"]=(g.index>="20260829").astype(float)
g["lper"]=np.log(g["paid"]/g["n"])
g["disp3"]=g["disp_hit"].rolling(3).mean().shift(1); g["disp7"]=g["disp_hit"].rolling(7).mean().shift(1)
g["roi3"]=g["roi"].rolling(3).mean().shift(1); g["roi7"]=g["roi"].rolling(7).mean().shift(1)
# 先読みプラセボ: 「これから7日」の表示的中
g["disp7F"]=g["disp_hit"][::-1].rolling(7).mean()[::-1].shift(-1)
g["roi3F"]=g["roi"][::-1].rolling(3).mean()[::-1].shift(-1)
base=["lnn","share_fin","share_hp","t","era"]
def coef(df,cols,key,y="ln"):
    df=df.dropna(subset=cols+[y]); X,names=dmat(df,cols,None); res,_,_=ols(df[y].values,X,names); return res[key],len(df)
G=g.dropna(subset=["big10_lag"]).copy()
v,n=coef(G,base+["lnn"] if False else base,"lnn","lper"); print(f"1商品あたり lnn {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}] n={n}")
print("\n### 置換検定 G4")
obs,_=coef(G,base+["big10_lag"],"big10_lag")
rng=np.random.default_rng(7); vals=G["big10_lag"].values.copy(); null=[]
for _ in range(3000):
    s=G.copy(); s["big10_lag"]=rng.permutation(vals); null.append(coef(s,base+["big10_lag"],"big10_lag")[0][0])
null=np.array(null); print(f"  観測 {obs[0]:+.3f}  帰無 95%上側 {np.percentile(null,97.5):+.3f}  片側p={(null>=obs[0]).mean():.4f}")
print("\n### disp7 / roi の前向き・後ろ向きプラセボ（先読みが効くならトレンドの写し）")
for k in ["disp7","disp7F","roi_lag","roi3","roi3F","roi7"]:
    v,n=coef(g,base+[k],k); print(f"  {k:<8} n={n} {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
print("\n### disp7 を trend 無しで / era 無しで / 型ラボ期だけで")
for cols,lab in [ (["lnn","share_fin","share_hp","era","disp7"],"t 無し"),
                  (["lnn","share_fin","share_hp","t","disp7"],"era 無し"),
                  (["lnn","share_fin","share_hp","t","era","disp7","big10_lag"],"+前日10万")]:
    v,n=coef(g,cols,"disp7"); print(f"  {lab:<12} n={n} disp7 {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
tl=g[g.index>="20260829"]
v,n=coef(tl,["lnn","share_fin","share_hp","t","disp7"],"disp7"); print(f"  型ラボ期のみ n={n} disp7 {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
print("\n### 置換検定 disp7（ブロック置換: 7日ブロックを回す循環シフト）")
s=g.dropna(subset=["disp7","ln","lnn","share_fin","share_hp"]).copy()
obs2,_=coef(s,base+["disp7"],"disp7")
null2=[]
x=s["disp7"].values
for k in range(1,len(x)):
    s2=s.copy(); s2["disp7"]=np.roll(x,k); null2.append(coef(s2,base+["disp7"],"disp7")[0][0])
null2=np.array(null2)
print(f"  観測 {obs2[0]:+.3f}  循環シフト {len(null2)}通りの分布 中央 {np.median(null2):+.3f} 95%上側 {np.percentile(null2,97.5):+.3f}  片側p={(null2>=obs2[0]).mean():.4f}")
print("\n### 生: 直近7日表示的中率の三分位 × 日売上")
s["q"]=pd.qcut(s["disp7"],3,labels=False)
print(s.groupby("q").agg(n=("ln","size"),disp7=("disp7","median"),paid=("paid","mean"),nprod=("n","mean"),per=("lper",lambda x:np.exp(x.mean()))).round(3).to_string())
