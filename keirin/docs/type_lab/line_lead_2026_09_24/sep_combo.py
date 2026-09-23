import pandas as pd, numpy as np
S=pd.read_pickle("SEP/legs_sep.pkl"); S=S[S.lead_rk>=5]
r=S.assign(ho=S.odds.where(S.hit)).groupby(["race_key","date"]).agg(n=("hit","size"),hit=("hit","max"),odds=("ho","max")).reset_index()
r["stake"]=(10000/r.n//100*100); r["inv"]=r.stake*r.n; r["ret"]=np.where(r.hit,r.odds*r.stake,0).round()
fmt=lambda i:f"{i[4:6]}/{i[6:]}"
d=r.groupby("date").agg(レース=("race_key","size"),投資=("inv","sum"),的中=("hit","sum"),払戻=("ret","sum"),最高払戻=("ret","max"))
d["回収率"]=(d.払戻/d.投資*100).round(1); d.index=[fmt(i) for i in d.index]
print("== ③ 単独（1レース1万円の均等）=="); print(d.astype({"投資":int,"払戻":int,"最高払戻":int}).to_string())
t=d.sum(); print(f"合計 レース{int(t.レース)} 投資{int(t.投資):,} 的中{int(t.的中)} 払戻{int(t.払戻):,} 回収率{t.払戻/t.投資*100:.1f}%  100%超えの日 {(d.回収率>=100).sum()}/{len(d)}")
# --- 組み合わせ（9/1〜9/22）
sub=pd.read_csv("SEP/subs.csv"); sub=sub[(sub.status=="published")&sub.settled_at.notna()&(sub.race_key<"20260923")].copy()
sub["date"]=sub.race_key.str[:8]; sub["hitd"]=sub.settled_payout>sub.settled_bet
r2=r[r.date<"20260923"].copy(); X=set(r2.race_key)
cur=sub.groupby("date").agg(件数=("race_key","size"),投資=("settled_bet","sum"),的中=("hitd","sum"),払戻=("settled_payout","sum"))
keep=sub[~sub.race_key.isin(X)]
new=pd.concat([keep[["date","settled_bet","settled_payout","hitd"]].rename(columns={"settled_bet":"inv","settled_payout":"ret","hitd":"h"}).assign(src="型ラボ"),
               r2[["date","inv","ret","hit"]].rename(columns={"hit":"h"}).assign(src="③")])
com=new.groupby("date").agg(件数=("inv","size"),投資=("inv","sum"),的中=("h","sum"),払戻=("ret","sum"))
rep=sub[sub.race_key.isin(X)]
print(f"\n③の対象で型ラボ商品を売っていたレース: {rep.race_key.nunique()}（その商品の成績: 投資{int(rep.settled_bet.sum()):,} 払戻{int(rep.settled_payout.sum()):,} 回収率{rep.settled_payout.sum()/rep.settled_bet.sum()*100:.1f}%）")
out=pd.DataFrame({"現行_件数":cur.件数,"現行_投資":cur.投資,"現行_的中":cur.的中,"現行_回収率":(cur.払戻/cur.投資*100).round(1),
                  "組合せ_件数":com.件数,"組合せ_投資":com.投資,"組合せ_的中":com.的中,"組合せ_回収率":(com.払戻/com.投資*100).round(1),
                  "収支差":(com.払戻-com.投資)-(cur.払戻-cur.投資)})
out.index=[fmt(i) for i in out.index]
print("\n== 現行（実際に売った型ラボ） vs 組み合わせ（③＋残りは型ラボ）  9/1〜9/22 ==")
print(out.astype({"現行_投資":int,"組合せ_投資":int,"収支差":int}).to_string())
for nm,x in [("現行",cur),("組合せ",com)]:
    print(f"{nm}: 件数{int(x.件数.sum())} 投資{int(x.投資.sum()):,} 的中{int(x.的中.sum())} 払戻{int(x.払戻.sum()):,} 回収率{x.払戻.sum()/x.投資.sum()*100:.1f}% 収支{int(x.払戻.sum()-x.投資.sum()):,} 100%超えの日{(x.払戻>=x.投資).sum()}/{len(x)}")
