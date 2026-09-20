import sys, numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
m=pd.read_pickle(f"{OUT}/market_merged.pkl")
def lg(p): p=np.clip(p,1e-6,1-1e-6); return np.log(p/(1-p))
m["L_mkt_w"],m["L_mdl_w"]=lg(m.mkt_pw),lg(m.mdl_pw)
m["L_mkt_3"],m["L_mdl_3"]=lg(m.mkt_p3),lg(m.mdl_p3)
m["ym"]=m.race_date.str[:7]

WINS=sorted(m.window.unique())
rng=np.random.default_rng(7)
def boot(d, col_a, col_b, y, B=2000):
    """レース単位 bootstrap で logloss 差 (a - b) の CI。"""
    rks=d.race_key.unique(); idx={k:i for i,k in enumerate(rks)}
    grp=d.race_key.map(idx).values
    la=-(d[y]*np.log(d[col_a])+(1-d[y])*np.log(1-d[col_a])).values
    lb=-(d[y]*np.log(d[col_b])+(1-d[y])*np.log(1-d[col_b])).values
    diff=la-lb
    sa=np.bincount(grp,weights=diff,minlength=len(rks)); cnt=np.bincount(grp,minlength=len(rks))
    out=[]
    for _ in range(B):
        s=rng.integers(0,len(rks),len(rks))
        out.append(sa[s].sum()/cnt[s].sum())
    return float(diff.mean()), float(np.percentile(out,2.5)), float(np.percentile(out,97.5))

print("=== walk-forward 条件付きロジスティック（学習=それ以前の窓・評価=その窓）===")
for tag,(Lk,Lm,y) in {"win":("L_mkt_w","L_mdl_w","win_flag"),
                      "top3":("L_mkt_3","L_mdl_3","top3_flag")}.items():
    rows=[]; preds=[]
    for i,w in enumerate(WINS):
        if i<2: continue
        tr=m[m.window.isin(WINS[:i])]; te=m[m.window==w].copy()
        if len(te)<500: continue
        r={"target":tag,"window":w,"n_te_race":te.race_key.nunique()}
        for nm,cols in (("mkt",[Lk]),("mdl",[Lm]),("both",[Lk,Lm])):
            f=LogisticRegression(max_iter=1000,C=1e6).fit(tr[cols],tr[y])
            p=np.clip(f.predict_proba(te[cols])[:,1],1e-6,1-1e-6)
            te[f"p_{nm}"]=p
            r[f"ll_{nm}"]=log_loss(te[y],p); r[f"auc_{nm}"]=roc_auc_score(te[y],p)
            if nm=="both": r["b_mkt"],r["b_mdl"]=f.coef_[0]
            if nm=="mdl": r["b_mdl_alone"]=f.coef_[0][0]
        rows.append(r); preds.append(te)
    df=pd.DataFrame(rows)
    df["d_mkt2both"]=df.ll_both-df.ll_mkt; df["d_mdl2both"]=df.ll_both-df.ll_mdl
    print(f"\n--- {tag} ---")
    print(df.to_string(index=False,float_format=lambda x:f"{x:.4f}"))
    P=pd.concat(preds)
    for a,b,lab in (("p_both","p_mkt","市場単独→市場+モデル"),("p_both","p_mdl","モデル単独→市場+モデル")):
        d,lo,hi=boot(P,a,b,y)
        print(f"  Δlogloss {lab}: {d:+.5f}  CI95 [{lo:+.5f}, {hi:+.5f}]  (負なら改善)")
    print(f"  pooled AUC: mkt {roc_auc_score(P[y],P.p_mkt):.4f} / mdl {roc_auc_score(P[y],P.p_mdl):.4f} / both {roc_auc_score(P[y],P.p_both):.4f}")
    P.to_pickle(f"{OUT}/incr_{tag}.pkl")
