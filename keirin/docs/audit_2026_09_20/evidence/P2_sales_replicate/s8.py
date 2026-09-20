from common import *
d = pd.read_pickle("ds.pkl")
t=d[d["date"]>="20260829"].copy()
t["sub_ts"]=pd.to_datetime(t["submitted_at"],format="mixed",errors="coerce")
hp=t[t["origin"]=="highpay_fill"].copy().sort_values(["date","sub_ts"])
hp["slot"]=hp.groupby("date").cumcount()+1
day=t.groupby("date").agg(n_all=("paid","size"),paid_all=("paid","sum"),
    paid_oth=("paid","sum"))
# 非高額枠の日合計（正規化の分母）
oth=t[t["origin"]!="highpay_fill"].groupby("date")["paid"].agg(n_oth="size",paid_oth="sum")
g=hp.groupby("date").agg(n_hp=("paid","size"),hp_paid=("paid","sum"),hp_per=("paid","mean")).join(oth).join(day[["n_all","paid_all"]])
g["era"]=np.where(g.index<"20260912","5枠","10枠")
g["oth_per"]=g["paid_oth"]/g["n_oth"]
g["rel"]=g["hp_per"]/g["oth_per"]
print("### 高額枠(origin=highpay_fill) 日次")
print(g.round(1).to_string())
print("\n### 期間比較（*所要の希釈テスト*）")
for nm,s in [("A: 5枠 08/29-09/11",g[g.index<"20260912"]),("B: 10枠 09/12-09/18",g[g.index>="20260912"])]:
    print(f"  {nm}: 日数={len(s)}  高額枠本数/日={s['n_hp'].mean():.2f}  1本あたり={s['hp_paid'].sum()/s['n_hp'].sum():,.0f}pt  "
          f"高額枠売上/日={s['hp_paid'].mean():,.0f}pt  非高額枠1本あたり={s['paid_oth'].sum()/s['n_oth'].sum():,.0f}pt  "
          f"相対={s['rel'].mean():.2f}  日売上={s['paid_all'].mean():,.0f}pt")
print("\n### スロット別（origin=highpay_fill）")
s=hp.groupby("slot").agg(n=("paid","size"),paid=("paid","mean"),med=("paid","median"),sold=("paid",lambda x:(x>0).mean()))
print(s.round(1).to_string())
print("\n### 10枠期 内部: 1-5本目 vs 6本目以降（同日内なので日FEが効く）")
e=hp[hp["date"]>="20260912"]
A=e[e["slot"]<=5]["paid"].values; B=e[e["slot"]>=6]["paid"].values
print(f"  1-5: n={len(A)} 平均={A.mean():.0f} 中央={np.median(A):.0f}")
print(f"  6+ : n={len(B)} 平均={B.mean():.0f} 中央={np.median(B):.0f}")
rng=np.random.default_rng(1)
bs=[rng.choice(A,len(A),True).mean()-rng.choice(B,len(B),True).mean() for _ in range(6000)]
print(f"  差={A.mean()-B.mean():.0f}  CI95=[{np.percentile(bs,2.5):.0f}, {np.percentile(bs,97.5):.0f}]")
# 同日ペアでの差（日FE）
pair=[]
for dt,s2 in e.groupby("date"):
    a=s2[s2["slot"]<=5]["paid"]; b=s2[s2["slot"]>=6]["paid"]
    if len(a) and len(b): pair.append((dt,len(a),len(b),a.mean(),b.mean()))
print("  日ごと:", [(p[0],int(p[1]),int(p[2]),round(p[3]),round(p[4])) for p in pair])
print("\n### 6本目以降の限界売上（10枠期のみ）")
print(f"  6本目以降の合計 = {B.sum():,.0f}pt / 6日 = {B.sum()/6:,.0f}pt/日")
print(f"  もし 5枠のままなら失う額 ≈ {B.sum()/6:,.0f}pt/日（共食いがゼロと仮定＝上限）")
# 共食い: 10枠期の非高額枠1本あたりは5枠期より落ちたか
