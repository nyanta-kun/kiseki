import pandas as pd, numpy as np, pickle, glob
U=pd.read_pickle("LN/units_occ.pkl")
OD=pickle.load(open("odds.pkl","rb")); mo=pd.read_csv("LN/miss_odds.csv")
for rk,g in mo.groupby("race_key"): OD[rk]=dict(zip(g.combination,g.odds_value))
fl=pd.concat([pd.read_csv(f) for f in glob.glob("LN/fill_*.csv")])
complete=set(fl.race_key)
for rk,g in fl.groupby("race_key"): OD[rk]={**OD.get(rk,{}),**dict(zip(g.combination,g.odds_value))}
res=pd.read_pickle("LN/full.pkl").set_index("race_key").res
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(U.race_key))][["race_key","frame_no","line_group","line_pos","race_point"]]; LP={k:g for k,g in fe.groupby("race_key")}
BANDS=[0,10,25,50,100,300,1e9]; bl=lambda o:np.digitize(o,BANDS)-1
# 基準: オッズがそろったレースで、帯ごとの「1目あたりの的中率」と「市場見込み 0.75/odds 平均」
cnt=np.zeros(6); hit=np.zeros(6); imp=np.zeros(6)
for rk in complete:
    od=OD[rk]; r_=res.get(rk)
    for c,o in od.items():
        if not o or o<=0: continue
        b=bl(o); cnt[b]+=1; imp[b]+=0.75/o; hit[b]+=(c==r_)
base_hit=hit/cnt; base_ratio=hit/imp
print("基準（全目・オッズ帯別）: 帯 [〜10,10-25,25-50,50-100,100-300,300〜]")
print("  1目の的中率",np.round(base_hit*100,3)); print("  発生倍率（実際÷市場見込み）",np.round(base_ratio,2), " 件数",cnt.astype(int))
rows=[]
for x in U.itertuples():
    g=LP[x.race_key]; cars=g.frame_no.astype(int).tolist(); od=OD.get(x.race_key,{})
    for lg,h in g.groupby("line_group"):
        if len(h)!=x.line_size or abs(h.race_point.sum()-x.line_sum)>1e-6: continue
        h=h.sort_values("line_pos"); a,b=int(h.frame_no.iloc[0]),int(h.frame_no.iloc[1])
        os_=np.array([od.get(f"{a}-{b}-{c}",np.nan) for c in cars if c not in (a,b)],float)
        ok=~np.isnan(os_)
        rows.append(dict(i=x.Index,miss2=int((~ok).sum()),impl2=(0.75/os_[ok]).sum(),bandexp=sum(base_hit[bl(o)] for o in os_[ok])))
        break
O=pd.DataFrame(rows).set_index("i"); U=U.drop(columns=["impl"],errors="ignore").join(O)
print("\nオッズがまだ欠けている目の割合:",f"{U.miss2.sum()/(5*len(U)):.2%}")
rules={"② 基準":U.index==U.index,"③ 先頭の得点5位以下":U.lead_rk>=5,"A ラインの得点合計≤175":U.line_sum<=175,"D ③∧2車ライン":(U.lead_rk>=5)&(U.line_size==2),
       "（逆）先頭の得点2-4位":U.lead_rk<=4,"（逆）ラインの得点合計>214":U.line_sum>214,"（逆）3車ライン":U.line_size==3}
rng=np.random.default_rng(0)
print("\n発生倍率A = 的中 ÷ 市場見込み(Σ0.75/オッズ)  /  発生倍率B = 的中 ÷ 同じオッズ帯の目の実績的中率の和")
for nm,m in rules.items():
    o=[]
    for w in ["探索","確認"]:
        q=U[m&(U.win==w)]; dd=q.groupby("date").agg(h=("hit","sum"),e=("impl2","sum"),b=("bandexp","sum")).values
        bs=[dd[i].sum(0) for i in [rng.integers(0,len(dd),len(dd)) for _ in range(1000)]]
        la,ha=np.percentile([b_[0]/b_[1] for b_ in bs],[2.5,97.5]); lb,hb=np.percentile([b_[0]/b_[2] for b_ in bs],[2.5,97.5])
        o.append(f"{w} 的中{q.hit.mean():.2%} A {q.hit.sum()/q.impl2.sum():.2f}[{la:.2f},{ha:.2f}] B {q.hit.sum()/q.bandexp.sum():.2f}[{lb:.2f},{hb:.2f}]")
    print(f"{nm:20s}"," | ".join(o))
U.to_pickle("LN/units_occ2.pkl")
