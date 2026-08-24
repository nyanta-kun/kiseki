"""(1) 決勝で 7S 相当(三連複軸2車総流し)がいくら稼ぐか (2) 別/同ライン判定の頑健性。"""
import pickle, numpy as np, pandas as pd
rng=np.random.default_rng(909)
F=pickle.load(open("/tmp/keirin_upset_frame.pkl","rb")).set_index("race_key")
feat=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key")
O=pd.read_pickle("/tmp/overlap.pkl").set_index("race_key")
odds=pickle.load(open("/tmp/keirin_trio_odds.pkl","rb"))
odds["key"]=odds.combination.apply(lambda s:tuple(sorted(int(x) for x in s.replace("=","-").split("-"))))
KES={"決勝","チャレンジ決勝"}
rt=feat.race_type.astype(str)
tgt=[rk for rk in F.index if rt.get(rk,"") in KES and rk in O.index]
print(f"決勝 {len(tgt):,}R")
ax=F.loc[tgt,["axis1","axis2","act"]]
grid={rk:dict(zip(g.key,g.odds_value)) for rk,g in odds[odds.race_key.isin(tgt)].groupby("race_key")}
rows=[]
for rk in tgt:
    g=grid.get(rk)
    if not g: continue
    a1,a2=int(ax.axis1[rk]),int(ax.axis2[rk]); a=ax.act[rk]
    legs=[k for k in g if a1 in k and a2 in k]
    if len(legs)<3: continue
    hit=1 if a in legs else 0
    rows.append(dict(rk=rk,n=len(legs),hit=hit,pay=(g[a]*100 if hit else 0.0),
                     cross=bool(O.cross.get(rk,True))))
D=pd.DataFrame(rows)
def rep(s,l):
    R=len(s); inv=s.n.sum()*100; pay=s.pay.values; h=int(s.hit.sum())
    b=[pay[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1500)]
    print(f"  {l:<26} {R:5d}R 的中 {h/R*100:5.2f}% ROI {pay.sum()/inv*100:5.1f}% "
          f"CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}] 払戻中央 {int(np.median(pay[pay>0])*20):>7,}円")
print("\n■ 7S 相当（三連複・軸2車総流し5点）を決勝で買うと")
rep(D,"決勝すべて"); rep(D[D.cross],"決勝×別ライン"); rep(D[~D.cross],"決勝×同ライン")
print("\n  （比較）7T1 決勝×別ライン ROI 106.3% / 7T3 決勝×同ライン ROI 97.2%")

# (2) 別/同ライン判定の頑健性
d=pickle.load(open("/tmp/keirin_upset_ds.pkl","rb"))
E=d["E"].merge(d["pred"],on=["race_key","frame_no"],how="inner")
E=E[E.race_key.isin(set(tgt))]
fl=[]
for rk,g in E.groupby("race_key"):
    if len(g)!=7: continue
    cars=g.frame_no.astype(int).tolist(); p3=dict(zip(cars,g.pp3.astype(float)))
    lg=dict(zip(cars,g.line_group)); o=sorted(cars,key=lambda c:-p3[c])
    def cr(x,y): return not(lg.get(x) is not None and lg.get(x)==lg.get(y))
    base=cr(o[0],o[1]); alt=cr(o[0],o[2])   # 軸2を3番手に替えたら
    gap=(p3[o[1]]-p3[o[2]])/max(p3[o[1]],1e-9)
    fl.append(dict(rk=rk,base=base,alt=alt,flip=base!=alt,gap=gap))
FL=pd.DataFrame(fl)
print(f"\n■ 別/同ライン判定の頑健性（決勝 {len(FL):,}R）")
print(f"  軸2を p3 の3番手に替えると判定が反転する割合 : {FL.flip.mean()*100:.1f}%")
q=pd.qcut(FL.gap,4,labels=["2位-3位差 小","やや小","やや大","大"])
print(FL.groupby(q,observed=True).agg(n=("flip","size"),反転率=("flip",lambda s:round(s.mean()*100,1))).to_string())
