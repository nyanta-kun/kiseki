import pandas as pd, numpy as np
d=pd.read_pickle("LN/full.pkl"); ln=pd.read_pickle("LN/d.pkl")[["race_key","comp","rp12_same","rp1_pos","rp1_line_size","rp_gap12","rp_gap13"]]
d=d.merge(ln,on="race_key",how="left")
k70=pd.read_pickle("LN/d2.pkl")[["race_key","k_現行の選別"]]; d=d.merge(k70,on="race_key",how="left")
d=d[d.L2st>0].copy()
# 事前に決めた選び方（すべて発走前に分かる量）
d["s_upset"]=-d["k_現行の選別"].fillna(99)             # 現行の波乱選別（払戻欠けだったレースは選ばれない＝不利側）
d["s_rp1weak"]=-(d.rp1_line_size.fillna(3))+ (d.rp1_pos=="単騎")*5   # 得点1位のラインが小さいほど高い
d["s_close"]=-d.rp_gap13                               # 得点が拮抗
d["s_sep"]=(~d.rp12_same.astype(bool)).astype(int)*10 - d.rp_gap12   # 得点1・2位が別ライン・差が小さい
rng=np.random.default_rng(0)
def run(score,K,w):
    W=d[d.win==w].copy(); W["k"]=W[score].groupby(W.race_date).rank(ascending=False,method="first") if score else rng.random(len(W))
    if not score: W["k"]=W.k.groupby(W.race_date).rank(method="first")
    s=W[W.k<=K]; dd=s.groupby("race_date")[["L2st","L2rt"]].sum()
    return s.L2rt.sum()/s.L2st.sum(), (dd.L2rt>=dd.L2st).mean(), (s.L2rt>0).mean(), s.L2st.mean()/1000
for K in [5,8]:
    print(f"--- 1日{K}本 × L2（得点1位のいないライン 先頭→番手→流し）")
    for w in ["探索","確認"]:
        cr=[run(None,K,w) for _ in range(20)]; print(f"  無作為20本 {w}: ROI中央 {np.median([c[0] for c in cr]):.1%} 百超日 {np.median([c[1] for c in cr]):.1%}")
    for sc,lab in [("s_upset","現行の波乱選別"),("s_rp1weak","得点1位のラインが小さい"),("s_close","得点が拮抗(1位-3位差小)"),("s_sep","得点1・2位が別ライン×差小")]:
        o=[]
        for w in ["探索","確認"]:
            roi,p,h,pt=run(sc,K,w); o.append(f"{w} 点{pt:.1f} 的中{h:.1%} ROI{roi:.1%} 百超日{p:.1%}")
        print(f"  {lab:22s}", " | ".join(o))
