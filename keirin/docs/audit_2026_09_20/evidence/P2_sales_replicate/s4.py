from common import *
d = pd.read_pickle("ds.pkl"); d["lpay"]=np.log(d["plan_pay"])
t = d[(d["date"]>="20260829") & d["plan_pay"].notna()].copy(); t["conf_f"]=t["conf"].astype(float)
print("lpay の分散分解: total sd=%.3f  within-plan sd=%.3f (%.0f%%)" % (
  t["lpay"].std(), t.groupby("plan")["lpay"].transform(lambda x:x-x.mean()).std(),
  100*t.groupby("plan")["lpay"].transform(lambda x:x-x.mean()).var()/t["lpay"].var()))

def run(df, cols, fe, y="lpaid", label=""):
    df = df.dropna(subset=cols+[y]).copy()
    if len(df)<30: print(f"-- {label}: n={len(df)} 少なすぎ"); return
    X,names = dmat(df, cols, fe); res,_,_ = ols(df[y].values, X, names)
    print(f"-- {label}  n={len(df)}: " + "  ".join(f"{c} {res[c][0]:+.3f} [{res[c][1]:+.3f},{res[c][2]:+.3f}]" for c in cols))

print("\n### 反証2: 「高配当ファミリー」内 / 「本線ファミリー」内 で弾力性は残るか")
hi = t[t["plan_pay"]>=90000]; lo = t[t["plan_pay"]<60000]
print(f"  hi n={len(hi)} (plans {sorted(hi['plan'].unique())})")
print(f"  lo n={len(lo)} (plans {sorted(lo['plan'].unique())})")
run(hi, ["lpay","conf_f"], ["date","seg"], label="高配当ファミリーのみ")
run(lo, ["lpay","conf_f"], ["date","seg"], label="本線ファミリーのみ")
run(hi, ["lpay"], ["date","seg","plan"], label="高配当ファミリー + plan FE")
run(lo, ["lpay","conf_f"], ["date","seg","plan"], label="本線ファミリー + plan FE")

print("\n### 反証2b: 二値ダミー（高配当ファミリーか）だけで lpay を置き換えられるか")
t["fam_hi"]=(t["plan_pay"]>=90000).astype(float)
for cols,lab in [(["lpay","conf_f"],"lpay のみ"),(["fam_hi","conf_f"],"ファミリーダミーのみ"),
                 (["lpay","fam_hi","conf_f"],"両方")]:
    df=t.dropna(subset=cols); X,names=dmat(df,cols,["date","seg"]); res,b,_=ols(df["lpaid"].values,X,names)
    r=df["lpaid"].values-X@b
    print(f"  {lab:<18} R2={1-r.var()/df['lpaid'].var():.4f}  " + "  ".join(f"{c} {res[c][0]:+.3f}[{res[c][1]:+.2f},{res[c][2]:+.2f}]" for c in cols))

print("\n### 反証2c: 高配当ファミリー内のプラン別（同日同種別の残差平均）")
df=t.dropna(subset=["lpaid"]); X,names=dmat(df,["conf_f"],["date","seg"])
b,*_=np.linalg.lstsq(X,df["lpaid"].values,rcond=None); df=df.assign(resid=df["lpaid"].values-X@b)
g=df.groupby("plan").agg(n=("resid","size"),pay=("plan_pay","median"),resid=("resid","mean"),paid=("paid","mean"))
print(g[g["n"]>=6].sort_values("pay").round(3).to_string())
