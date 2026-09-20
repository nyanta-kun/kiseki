from common import *
d = pd.read_pickle("ds.pkl")
d["lpay"]=np.log(d["plan_pay"])
t = d[(d["date"]>="20260829") & d["plan_pay"].notna()].copy()
t["conf_f"]=t["conf"].astype(float)
t["lnlegs"]=np.log(t["n_legs"].clip(lower=1))
print("n =", len(t))

def run(df, cols, fe, y="lpaid", label=""):
    df = df.dropna(subset=cols+[y]).copy()
    X,names = dmat(df, cols, fe)
    res,b,se = ols(df[y].values, X, names)
    print(f"-- {label}  n={len(df)} k={X.shape[1]}")
    for c in cols:
        v=res[c]; print(f"   {c:<14} {v[0]:+.3f} [{v[1]:+.3f}, {v[2]:+.3f}]")

print("\n### G1 再現: 日FE + 種別 + 自信あり")
run(t, ["lpay","conf_f"], ["date","seg"], label="G仕様に近い（plan FEなし）")
run(t, ["lpay","conf_f","lnlegs"], ["date","seg"], label="+ log点数")
print("\n### 反証1: プラン固定効果を入れる（＝タイトル文言を一定にする）")
run(t, ["lpay","conf_f"], ["date","seg","plan"], label="+ plan FE")
run(t, ["lpay","conf_f","lnlegs"], ["date","seg","plan"], label="+ plan FE + log点数")
print("\n### 反証1b: プラン FE だけ（lpay なし） vs lpay だけ の説明力")
for cols,fe,lab in [ (["conf_f"],["date","seg"],"date+seg"),
                     (["lpay","conf_f"],["date","seg"],"+lpay"),
                     (["conf_f"],["date","seg","plan"],"+planFE"),
                     (["lpay","conf_f"],["date","seg","plan"],"+planFE+lpay")]:
    df=t.dropna(subset=cols+["lpaid"]); X,_=dmat(df,cols,fe)
    b,*_=np.linalg.lstsq(X,df["lpaid"].values,rcond=None); r=df["lpaid"].values-X@b
    print(f"   {lab:<18} R2={1-r.var()/df['lpaid'].var():.4f}  k={X.shape[1]}")

print("\n### 反証1c: 同一プラン内の計画払戻の分位（生）")
for p in ["F_sign","A_ana","E_hit","C_hit","F_hit","B_hit","T_upset"]:
    s=t[t["plan"]==p]
    if len(s)<25: continue
    s=s.copy(); s["q"]=pd.qcut(s["plan_pay"],3,labels=False,duplicates="drop")
    g=s.groupby("q").agg(n=("paid","size"),pay=("plan_pay","median"),paid=("paid","mean"))
    print(f"  {p}: " + " | ".join(f"q{int(i)} n={int(r.n)} pay={r.pay:,.0f} paid={r.paid:,.0f}" for i,r in g.iterrows()))
