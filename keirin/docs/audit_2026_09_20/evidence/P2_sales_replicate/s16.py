from common import *
d=pd.read_pickle("ds.pkl"); d["lpay"]=np.log(d["plan_pay"])
LONG={"A_ana","F_pay","T_upset"}|{f"{x}_{k}" for x in "ABCDEF" for k in ("sign","big")}
t=d[(d["date"]>="20260829")&d["plan_pay"].notna()].copy()
t["conf_f"]=t["conf"].astype(float); t["long_f"]=(t["plan"].isin(LONG)&~t["conf"]).astype(float)
print("### G1 の弾力性を「_sign→_big」で外挿して答え合わせ（同じアイコン・同じ供給源）")
hp=t[t["origin"]=="highpay_fill"].copy(); hp["big"]=hp["plan"].str.endswith("_big").astype(float)
X,names=dmat(hp,["big"],["date"]); res,_,_=ols(hp["lpaid"].values,X,names)
b=res["big"]; print(f"  実測 Δlog1p(有償pt) [_big − _sign, 日FE] = {b[0]:+.3f} [{b[1]:+.3f},{b[2]:+.3f}]  n={len(hp)}")
ratio=hp[hp['big']>0]['plan_pay'].median()/hp[hp['big']==0]['plan_pay'].median()
print(f"  計画払戻比 = ×{ratio:.2f} → G1(弾力性1.22) の予言は Δlog = {1.221*np.log(ratio):+.3f}")
print(f"  → 予言 {1.221*np.log(ratio):+.3f} は実測CI [{b[1]:+.3f},{b[2]:+.3f}] の " + ("外" if not (b[1]<=1.221*np.log(ratio)<=b[2]) else "中"))
print("\n### 同じ検算を「穴狙い内の全プラン」で")
hi=t[t["long_f"]>0].copy(); hi["q"]=pd.qcut(hi["plan_pay"],4,labels=False,duplicates="drop")
X,names=dmat(hi,["lpay"],["date"]); res,_,_=ols(hi["lpaid"].values,X,names); v=res["lpay"]
print(f"  穴狙い内 日FE の弾力性 = {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}] n={len(hi)}  (G1 の +1.22 は{'外' if not(v[1]<=1.221<=v[2]) else '中'})")
print("\n### G7 レース種別（商品属性を入れる前/後）")
for cols,lab in [(["conf_f"],"素（日FEのみ）"),(["conf_f","lpay"],"+計画払戻"),(["conf_f","long_f"],"+アイコン")]:
    X,names=dmat(t,cols,["date","seg"]); res,_,_=ols(t["lpaid"].values,X,names)
    out=" ".join(f"{k.split('=')[1]} {res[k][0]:+.2f}[{res[k][1]:+.2f},{res[k][2]:+.2f}]" for k in names if k.startswith("seg="))
    print(f"  {lab:<14} {out}")
print("\n### 週FE / 期間分割で G1 は変わるか（日FEは既にトレンドを吸収している確認）")
t["wk"]=pd.to_datetime(t["date"],format="%Y%m%d").dt.isocalendar().week.astype(str)
for fe,lab in [ (["wk","seg"],"週FE"), (["date","seg"],"日FE"), (["date","seg","plan"],"日FE+planFE")]:
    X,names=dmat(t,["lpay","conf_f"],fe); res,_,_=ols(t["lpaid"].values,X,names); v=res["lpay"]
    print(f"  {lab:<12} lpay {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
for w,lab in [("20260829","20260908"),("20260908","20260919")]:
    s=t[(t["date"]>=w)&(t["date"]<lab)]
    X,names=dmat(s,["lpay","conf_f"],["date","seg"]); res,_,_=ols(s["lpaid"].values,X,names); v=res["lpay"]
    print(f"  {w}-{lab} n={len(s)} lpay {v[0]:+.3f} [{v[1]:+.3f},{v[2]:+.3f}]")
