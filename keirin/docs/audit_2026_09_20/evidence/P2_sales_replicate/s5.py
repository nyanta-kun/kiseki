from common import *
d = pd.read_pickle("ds.pkl"); d["lpay"]=np.log(d["plan_pay"])
d["head"]=d["title"].fillna("").str.split("｜").str[0]
print("### 自然実験: F_hit のタイトルだけが変わった（2026-09-03「一撃の三連単」→「押さえの三連単」）")
f=d[(d["plan"]=="F_hit")].copy()
print(f.groupby(["head"]).agg(n=("paid","size"),d0=("date","min"),d1=("date","max"),
     pay=("plan_pay","median"),legs=("n_legs","median"),paid=("paid","mean"),med=("paid","median"),
     sold=("paid",lambda x:(x>0).mean())).round(2).to_string())
# 同日の他商品で正規化（日FEの代わり）: その日の F_hit 以外の平均で割る
day = d[d["date"]>="20260829"].groupby("date")["paid"].mean().rename("day_mean")
f=f.join(day,on="date"); f["rel"]=f["paid"]/f["day_mean"]
print("\n 同日全商品平均比 rel:")
print(f.groupby("head").agg(n=("rel","size"),rel=("rel","mean"),relmed=("rel","median")).round(3).to_string())
# 日数が違うので前後7日ずつに絞る
a=f[(f["date"]>="20260829")&(f["date"]<"20260903")]; b=f[(f["date"]>="20260903")&(f["date"]<"20260910")]
for nm,s in [("一撃期 08/29-09/02",a),("押さえ期 09/03-09/09",b)]:
    print(f"  {nm}: n={len(s)} paid平均={s['paid'].mean():.0f} rel={s['rel'].mean():.3f} 計画払戻中央={s['plan_pay'].median():,.0f}")

print("\n### タイトル頭（狙いの語）別・同日同種別の残差")
t=d[(d["date"]>="20260829")&d["plan_pay"].notna()].copy(); t["conf_f"]=t["conf"].astype(float)
X,names=dmat(t,["conf_f"],["date","seg"]); b,*_=np.linalg.lstsq(X,t["lpaid"].values,rcond=None)
t["resid"]=t["lpaid"].values-X@b
g=t.groupby("head").agg(n=("resid","size"),pay=("plan_pay","median"),resid=("resid","mean"),paid=("paid","mean"))
print(g[g["n"]>=8].sort_values("resid").round(3).to_string())
