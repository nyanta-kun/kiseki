import pandas as pd, numpy as np
r=pd.read_pickle("H1/races70.pkl")
fe=pd.read_pickle("/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl")
fe=fe[fe.race_key.isin(set(r.race_key))][["race_key","frame_no","race_point","line_group","line_pos","line_size","style"]]
rows=[]
for rk,g in fe.groupby("race_key"):
    g=g.copy()
    sizes=g.groupby("line_group").frame_no.size().sort_values(ascending=False)
    comp="-".join(str(s) for s in sizes.values)
    g=g.sort_values("race_point",ascending=False).reset_index(drop=True)
    a,b=g.iloc[0],g.iloc[1]
    pos=lambda x:"単騎" if x.line_size==1 else ("先頭" if x.line_pos==1 else ("番手" if x.line_pos==2 else "3番手以降"))
    same=a.line_group==b.line_group
    # 得点1位の所属ラインの得点合計順位
    ls=g.groupby("line_group").race_point.sum().rank(ascending=False,method="min")
    rows.append(dict(race_key=rk,comp=comp,n_lines=len(sizes),max_line=sizes.iloc[0],
        rp1_pos=pos(a),rp2_pos=pos(b),rp12_same=same,
        rp1_line_size=a.line_size,rp2_line_size=b.line_size,
        rp_gap12=a.race_point-b.race_point,rp_gap13=a.race_point-g.iloc[2].race_point,
        rp_gap23=b.race_point-g.iloc[2].race_point,
        rp1_line_rank=ls[a.line_group], rp1=int(a.frame_no), rp2=int(b.frame_no)))
L=pd.DataFrame(rows); d=r.merge(L,on="race_key")
top=[[int(x) for x in s.split("-")] for s in d.res]
d["rp1_in3"]=[a in t for a,t in zip(d.rp1,top)]; d["rp1_win"]=[a==t[0] for a,t in zip(d.rp1,top)]
d["rp12_in3"]=[a in t and b in t for a,b,t in zip(d.rp1,d.rp2,top)]
d["big"]=d.res_odds>=50; d["huge"]=d.res_odds>=100
d.to_pickle("LN/d.pkl"); print(len(d)); print(d.comp.value_counts().head(12))
