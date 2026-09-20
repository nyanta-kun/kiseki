from common import *
d=pd.read_pickle("ds.pkl"); d["lpay"]=np.log(d["plan_pay"])
LONG={"A_ana","F_pay","T_upset"}|{f"{t}_{k}" for t in "ABCDEF" for k in ("sign","big")}
t=d[(d["date"]>="20260829")&d["plan_pay"].notna()].copy()
t["conf_f"]=t["conf"].astype(float)
t["badge"]=np.where(t["conf"],"自信あり",np.where(t["plan"].isin(LONG),"穴狙い","なし"))
t["long_f"]=(t["plan"].isin(LONG)&~t["conf"]).astype(float)
print("### netkeirin 勝負アイコン（buyer が実際に見る唯一のメタ情報）別")
print(t.groupby("badge").agg(n=("paid","size"),pay=("plan_pay","median"),legs=("n_legs","median"),
   paid=("paid","mean"),med=("paid","median"),sold=("paid",lambda x:(x>0).mean())).round(1).to_string())
print("\n穴狙いアイコンが付くプラン集合 == 計画払戻90k以上の集合か:")
print("  穴狙い かつ pay<90k:", int(((t['long_f']>0)&(t['plan_pay']<90000)).sum()),
      " / アイコン無し かつ pay>=90k:", int(((t['long_f']==0)&(~t['conf'])&(t['plan_pay']>=90000)).sum()))
def run(df,cols,fe,lab,y="lpaid"):
    df=df.dropna(subset=cols+[y]); 
    if len(df)<30: print(f"  {lab}: n={len(df)} 少"); return
    X,names=dmat(df,cols,fe); res,b,_=ols(df[y].values,X,names); r=df[y].values-X@b
    print(f"  {lab:<28} n={len(df)} R2={1-r.var()/df[y].var():.4f}  " +
          "  ".join(f"{c} {res[c][0]:+.3f}[{res[c][1]:+.2f},{res[c][2]:+.2f}]" for c in cols))
print("\n### 反証3: 「計画払戻の数値」と「穴狙いアイコン＋タイトル文言」を分離できるか")
run(t,["lpay","conf_f"],["date","seg"],"lpay のみ")
run(t,["long_f","conf_f"],["date","seg"],"アイコンのみ")
run(t,["lpay","long_f","conf_f"],["date","seg"],"両方")
print("\n### 反証3b: アイコンを固定した中での弾力性")
run(t[t["long_f"]>0],["lpay"],["date","seg"],"穴狙いアイコン内 (100k-415k)")
run(t[(t["long_f"]==0)&(~t["conf"])],["lpay"],["date","seg"],"アイコン無し内 (20k-60k)")
print("\n### 反証3c: 段差の大きさ vs 傾きの大きさ")
hi=t[t["long_f"]>0]; lo=t[(t["long_f"]==0)&(~t["conf"])]
print(f"  アイコン無し: 計画払戻中央 {lo['plan_pay'].median():,.0f}  有償pt平均 {lo['paid'].mean():.0f}")
print(f"  穴狙い      : 計画払戻中央 {hi['plan_pay'].median():,.0f}  有償pt平均 {hi['paid'].mean():.0f}")
print(f"  → 払戻 ×{hi['plan_pay'].median()/lo['plan_pay'].median():.1f} で売上 ×{hi['paid'].mean()/lo['paid'].mean():.1f}  (弾力性換算 {np.log(hi['paid'].mean()/lo['paid'].mean())/np.log(hi['plan_pay'].median()/lo['plan_pay'].median()):.2f})")
q=hi.copy(); q["b"]=pd.qcut(q["plan_pay"],4,labels=False,duplicates="drop")
print("  穴狙い内の四分位:", " | ".join(f"pay={g['plan_pay'].median():,.0f} n={len(g)} paid={g['paid'].mean():.0f}" for _,g in q.groupby("b")))
