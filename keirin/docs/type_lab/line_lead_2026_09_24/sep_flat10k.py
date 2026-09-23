import pandas as pd, numpy as np
U=pd.read_pickle("SEP/units.pkl")
r=U.groupby("race_key").agg(day=("day","first"),n=("n3","sum"),hit=("hit3","max"),odds=("odds","first"),res=("res","first")).reset_index()
r=r[r.n>0]
r["stake"]=(10000/r.n//100*100).astype(int); r["invest"]=r.stake*r.n
r["ret"]=np.where(r.hit,r.odds*r.stake,0).round().astype(int)
d=r.groupby("day").agg(レース=("race_key","size"),投資=("invest","sum"),的中=("hit","sum"),払戻=("ret","sum"),最高払戻=("ret","max"))
d["回収率"]=(d.払戻/d.投資*100).round(1); d.index=[f"{i[4:6]}/{i[6:]}" for i in d.index]
print(d[["レース","投資","的中","払戻","回収率","最高払戻"]].to_string())
t=d.sum(); print(f"\n合計 レース{int(t.レース)} 投資{int(t.投資):,} 的中{int(t.的中)} 払戻{int(t.払戻):,} 回収率{t.払戻/t.投資*100:.1f}% 最高{int(d.最高払戻.max()):,}")
print("100%超えの日",(d.回収率>=100).sum(),"/",len(d)," 的中率",f"{r.hit.mean():.1%}"," 1レースの点数 中央",int(r.n.median()),"範囲",r.n.min(),"-",r.n.max()," 1点の金額 中央",int(r.stake.median()))
print("最大3本を除く回収率",f"{(r.ret.sum()-r.ret.nlargest(3).sum())/r.invest.sum()*100:.1f}%")
print("\n的中上位:"); print(r[r.hit].sort_values("ret",ascending=False).head(8)[["race_key","res","odds","n","stake","ret"]].to_string(index=False))
