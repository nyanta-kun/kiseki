from common import *
d = pd.read_pickle("ds.pkl"); d["lpay"]=np.log(d["plan_pay"])
t=d[d["date"]>="20260829"].copy()
t["sub_ts"]=pd.to_datetime(t["submitted_at"],format="mixed",errors="coerce")
hp=t[t["is_hp"]].copy().sort_values(["date","sub_ts"])
hp["slot"]=hp.groupby("date").cumcount()+1
print("### 高額枠の本数/日（5→10→5 の切替）")
g=hp.groupby("date").agg(n_hp=("paid","size"),paid=("paid","sum"),per=("paid","mean"))
allday=t.groupby("date").agg(n_all=("paid","size"),paid_all=("paid","sum"))
g=g.join(allday)
g["era"]=np.where(g.index<"20260912","5枠",np.where(g.index<"20260919","10枠","5枠(戻)"))
print(g.round(1).to_string())
print("\n### 期間別まとめ")
print(hp.assign(era=np.where(hp["date"]<"20260912","A:5枠(09/11まで)",np.where(hp["date"]<"20260919","B:10枠(09/12-18)","C:5枠戻(09/19)")))
      .groupby("era").agg(n=("paid","size"),days=("date","nunique"),per_day=("paid",lambda x:0),
        paid_mean=("paid","mean"),paid_med=("paid","median"),pay=("plan_pay","median"),
        sold=("paid",lambda x:(x>0).mean())).round(1).to_string())
print("\n### スロット番号別 1本あたり有償pt（希釈の直接測定・10枠期を含む全期間）")
s=hp.groupby("slot").agg(n=("paid","size"),paid=("paid","mean"),med=("paid","median"),
    pay=("plan_pay","median"),sold=("paid",lambda x:(x>0).mean()))
print(s.round(1).to_string())
print("\n### 6本目以降だけ（10枠期にしか存在しない）")
a=hp[hp["slot"]<=5]; b=hp[hp["slot"]>=6]
for nm,s2 in [("1-5本目",a),("6本目以降",b)]:
    print(f"  {nm}: n={len(s2)} paid平均={s2['paid'].mean():.0f} 中央={s2['paid'].median():.0f} 売れた率={(s2['paid']>0).mean():.3f} 計画払戻中央={s2['plan_pay'].median():,.0f}")
# 10枠期の内部で 1-5 vs 6+ を比べる（日FEが効く）
e=hp[(hp["date"]>="20260912")&(hp["date"]<"20260919")]
print("\n### 10枠期(09/12-18)の内部比較")
for nm,s2 in [("1-5本目",e[e["slot"]<=5]),("6本目以降",e[e["slot"]>=6])]:
    print(f"  {nm}: n={len(s2)} paid平均={s2['paid'].mean():.0f} 中央={s2['paid'].median():.0f} 売れた率={(s2['paid']>0).mean():.3f}")
# bootstrap diff
rng=np.random.default_rng(0)
A=e[e["slot"]<=5]["paid"].values; B=e[e["slot"]>=6]["paid"].values
if len(A)>5 and len(B)>5:
    bs=[rng.choice(A,len(A),True).mean()-rng.choice(B,len(B),True).mean() for _ in range(4000)]
    print(f"  差(1-5 − 6+) = {A.mean()-B.mean():.0f}  CI95 [{np.percentile(bs,2.5):.0f}, {np.percentile(bs,97.5):.0f}]")
print("\n### 10枠期 vs 5枠期: 高額枠1本あたり / 日合計")
for nm,s2 in [("5枠 08/29-09/11",hp[hp["date"]<"20260912"]),("10枠 09/12-18",e),("5枠戻 09/19-",hp[hp["date"]>="20260919"])]:
    dd=s2.groupby("date")["paid"].agg(["size","sum"])
    print(f"  {nm}: 日数={len(dd)} 本数/日={dd['size'].mean():.2f} 高額枠売上/日={dd['sum'].mean():,.0f}pt 1本あたり={s2['paid'].mean():.0f}pt")
