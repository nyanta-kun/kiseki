import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, log_loss
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
for tag,y in (("win","win_flag"),("top3","top3_flag")):
    P=pd.read_pickle(f"{OUT}/incr_{tag}.pkl")
    print(f"\n===== {tag} セグメント別（walk-forward 予測をプール）=====")
    rows=[]
    def add(nm,d):
        if d.race_key.nunique()<150: return
        rows.append(dict(seg=nm,n_race=d.race_key.nunique(),
            ll_mkt=log_loss(d[y],d.p_mkt),ll_mdl=log_loss(d[y],d.p_mdl),ll_both=log_loss(d[y],d.p_both),
            auc_mkt=roc_auc_score(d[y],d.p_mkt),auc_mdl=roc_auc_score(d[y],d.p_mdl),
            auc_both=roc_auc_score(d[y],d.p_both)))
    add("ALL",P)
    for g,d in P.groupby("grade"): add(f"grade {g}",d)
    P["girls"]=P.race_type.astype(str).str.startswith("ガールズ")
    for g,d in P.groupby("girls"): add(f"girls={g}",d)
    P["fin"]=P.race_type.astype(str).str.contains("決勝")
    for g,d in P.groupby("fin"): add(f"決勝系={g}",d)
    t=pd.DataFrame(rows); t["d_ll"]=t.ll_both-t.ll_mkt
    print(t.to_string(index=False,float_format=lambda x:f"{x:.4f}"))
