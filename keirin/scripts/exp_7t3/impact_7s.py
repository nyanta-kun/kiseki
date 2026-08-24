"""7S から決勝を外したときの影響（受け入れたトレードの記録）。"""
import pickle, numpy as np, pandas as pd
rng=np.random.default_rng(1212)
F=pickle.load(open("/tmp/keirin_upset_frame.pkl","rb")).set_index("race_key")
feat=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key")
odds=pickle.load(open("/tmp/keirin_trio_odds.pkl","rb"))
odds["key"]=odds.combination.apply(lambda s:tuple(sorted(int(x) for x in s.replace("=","-").split("-"))))
rt=feat.race_type.astype(str); KES={"決勝","チャレンジ決勝"}
grid={rk:dict(zip(g.key,g.odds_value)) for rk,g in odds.groupby("race_key")}
UNIT=2000   # 5点×2,000円=10,000円 → ガミ境界は 5.0倍
rows=[]
for rk in F.index:
    g=grid.get(rk)
    if not g: continue
    a1,a2=int(F.axis1[rk]),int(F.axis2[rk]); a=F.act[rk]
    legs=[k for k in g if a1 in k and a2 in k]
    if len(legs)<3: continue
    hit=1 if a in legs else 0
    pay=g[a]*UNIT if hit else 0.0
    rows.append(dict(rk=rk,date=F.race_date[rk],n=len(legs),hit=hit,pay=pay,
                     kes=rt.get(rk,"") in KES))
D=pd.DataFrame(rows); nd=D.date.nunique()
def rep(s,l):
    R=len(s); inv=s.n.sum()*UNIT; pay=s.pay.values; h=int(s.hit.sum())
    disp=int(((pay>0)&(pay>=inv/len(s))).sum())  # 表示的中=払戻>投資
    disp=int((pay>=10000).sum())
    b=[pay[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1500)]
    print(f"  {l:<24} {R:6d}R {R/nd:5.2f}件/日 的中 {h/R*100:5.2f}% "
          f"表示的中 {disp/R*100:5.2f}% ROI {pay.sum()/inv*100:5.1f}% CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}]")
print(f"7S 相当（三連複・軸2車総流し5点・2,000円/点）{nd}日")
rep(D,"現状（決勝を含む）")
rep(D[~D.kes],"決勝を外した後")
rep(D[D.kes],"（失う決勝ぶん）")
d=D[D.kes]
print(f"\n失うもの: {len(d)/nd:.2f}件/日 ・ 的中 {d.hit.mean()*100:.2f}% ・ "
      f"表示的中 {(d.pay>=10000).mean()*100:.2f}% ・ ROI {d.pay.sum()/(d.n.sum()*UNIT)*100:.1f}%")
