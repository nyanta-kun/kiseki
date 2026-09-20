import sys, numpy as np, pandas as pd
sys.path.insert(0,"/Users/ysuzuki/GitHub/kiseki/keirin")
from src.strategy_wt import rank_7t3_blend_probs
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
m=pd.read_pickle(f"{OUT}/market_merged.pkl")
od=pd.read_pickle(f"{OUT}/odds_trifecta_sample.pkl"); od=od[od.odds_value>0].copy()
od[["c1","c2","c3"]]=od.combination.str.split("-",expand=True).astype(int)

# 実際の決着（1-2-3着の車番）
fin=m[m.finish_order.between(1,3)].sort_values(["race_key","finish_order"])
win=fin.groupby("race_key").apply(lambda d:"-".join(str(int(x)) for x in d.frame_no),include_groups=False).rename("win_combo")
print("races with full 1-2-3:", len(win))

rows=[]
for rk,d in m.groupby("race_key"):
    p3={int(r.frame_no):float(r.p3) for r in d.itertuples()}
    pw={int(r.frame_no):float(r.pw) for r in d.itertuples()}
    lgp={int(r.frame_no):r.line_group for r in d.itertuples()}
    lps={int(r.frame_no):r.line_pos for r in d.itertuples()}
    b=rank_7t3_blend_probs(sorted(p3), pw, p3, line_group=lgp, line_pos=lps)
    for (x,y,z),v in b.items(): rows.append((rk,f"{x}-{y}-{z}",v))
bd=pd.DataFrame(rows,columns=["race_key","combination","p_mdl"])
print("board rows", len(bd))
j=od.merge(bd,on=["race_key","combination"],how="inner").merge(win,on="race_key",how="inner")
j["hit"]=(j.combination==j.win_combo).astype(int)
j["p_mkt"]=j.groupby("race_key")["odds_value"].transform(lambda s:(1/s)/ (1/s).sum())
j["ev"]=j.p_mdl*j.odds_value
j["ev_mkt"]=j.p_mkt*j.odds_value
print("joined", len(j), "races", j.race_key.nunique(), "hit rate", j.hit.mean())
# サニティ: 的中した目の実オッズ平均 vs 予測
print("\n実測: 買った目が当たる確率は 1/210=%.5f, 実 %.5f"%(1/210,j.hit.mean()))
print("board 確率の総和チェック:", j.groupby("race_key")["p_mdl"].sum().describe().round(4).to_dict())

def tab(col,label,q=10):
    d=j.copy(); d["b"]=pd.qcut(d[col],q,labels=False,duplicates="drop")
    t=d.groupby("b").agg(n=("hit","size"),ev=(col,"mean"),hits=("hit","sum"),
                         ret=("odds_value",lambda s:0))
    t["ret"]=d.groupby("b").apply(lambda x:(x.hit*x.odds_value).sum()/len(x),include_groups=False)
    t["hit_rate"]=t.hits/t.n
    t["mdl_p_mean"]=d.groupby("b")["p_mdl"].mean()
    t["odds_med"]=d.groupby("b")["odds_value"].median()
    print(f"\n=== {label} 分位 × 実回収率（1点100円相当・ret=平均払戻倍率）===")
    print(t.to_string(float_format=lambda x:f"{x:.4f}"))
tab("ev","モデルEV = p_model × 確定オッズ")
tab("ev_mkt","市場EV（= 1/Σ(1/O) 一定のはず・サニティ）")
tab("p_mdl","モデル確率")
# EV>1 の集合
for th in (1.0,1.2,1.5,2.0):
    s=j[j.ev>=th]
    if len(s)==0: continue
    print(f"EV>={th}: n={len(s):,} ({len(s)/len(j)*100:.1f}%)  回収率={((s.hit*s.odds_value).sum()/len(s)):.4f}  的中={s.hit.mean():.5f}  オッズ中央={s.odds_value.median():.1f}")
# 年で分ける
print("\n=== 年別 EV上位10%の回収率 ===")
for y,d in j.groupby(j.race_key.str[:4]):
    th=d.ev.quantile(0.9); s=d[d.ev>=th]
    print(f"{y}: n={len(s):,} 回収率={((s.hit*s.odds_value).sum()/len(s)):.4f}  全体={((d.hit*d.odds_value).sum()/len(d)):.4f}")
j.to_pickle(f"{OUT}/ev_joined.pkl")
