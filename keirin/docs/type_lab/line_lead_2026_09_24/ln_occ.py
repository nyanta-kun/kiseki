import pandas as pd, numpy as np, pickle
U=pd.read_pickle("LN/units4.pkl")
OD=pickle.load(open("odds.pkl","rb")); mo=pd.read_csv("LN/miss_odds.csv")
for rk,g in mo.groupby("race_key"): OD[rk]=dict(zip(g.combination,g.odds_value))
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(U.race_key))][["race_key","frame_no","line_group","line_pos","style","race_point"]]
# ユニットの5点のオッズを取り直す（先頭・番手の車番が要る）
LP={}
for rk,g in fe.groupby("race_key"):
    LP[rk]=g
full=pd.read_pickle("LN/full.pkl")[["race_key","res"]].set_index("race_key").res
rows=[]
for x in U.itertuples():
    g=LP[x.race_key]; cars=g.frame_no.astype(int).tolist(); od=OD.get(x.race_key,{})
    # このユニットの先頭と番手: 逃の先頭で、得点和・車数が一致するラインを特定
    for lg,h in g.groupby("line_group"):
        if len(h)!=x.line_size or abs(h.race_point.sum()-x.line_sum)>1e-6: continue
        h=h.sort_values("line_pos"); a,b=int(h.frame_no.iloc[0]),int(h.frame_no.iloc[1])
        legs=[f"{a}-{b}-{c}" for c in cars if c not in (a,b)]
        os_=[od.get(l,np.nan) for l in legs]
        rows.append(dict(i=x.Index,n_miss=int(np.isnan(os_).sum()),impl=np.nansum([0.75/o for o in os_]),min_odds=np.nanmin(os_) if not np.all(np.isnan(os_)) else np.nan))
        break
O=pd.DataFrame(rows).set_index("i"); U=U.join(O)
print("5点のうちオッズ欠け(25倍未満)がある単位の割合:",f"{(U.n_miss>0).mean():.1%}"," 平均欠け数",round(U.n_miss.mean(),2))
# 欠け(25倍未満)は保守的に 0.75/20 で埋める（=市場の見込みを高めに置き、発生倍率を低めに出す側）
U["impl"]=U.impl+U.n_miss*0.75/20
rules={"② 基準":U.index==U.index,"③ 先頭の得点5位以下":U.lead_rk>=5,"A ラインの得点合計≤175":U.line_sum<=175,"D ③∧2車ライン":(U.lead_rk>=5)&(U.line_size==2),
       "（逆）先頭の得点2-4位":U.lead_rk<=4,"（逆）ラインの得点合計>214":U.line_sum>214}
print("\n発生倍率 = 実際の的中数 ÷ 市場が見込む的中数(Σ0.75/オッズ)   ※1.00 なら市場どおり")
rng=np.random.default_rng(0)
for nm,m in rules.items():
    o=[]
    for w in ["探索","確認"]:
        q=U[m&(U.win==w)]; ratio=q.hit.sum()/q.impl.sum()
        dd=q.groupby("date").agg(h=("hit","sum"),e=("impl","sum")).values
        bs=[dd[i].sum(0) for i in [rng.integers(0,len(dd),len(dd)) for _ in range(1000)]]; lo,hi=np.percentile([b[0]/b[1] for b in bs],[2.5,97.5])
        o.append(f"{w} 的中{q.hit.mean():.2%} 市場見込み{q.impl.mean():.2%} 発生倍率{ratio:.2f}[{lo:.2f},{hi:.2f}]")
    print(f"{nm:22s}"," | ".join(o))
U.to_pickle("LN/units_occ.pkl")
