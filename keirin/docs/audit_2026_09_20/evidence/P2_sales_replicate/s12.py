from common import *
d=pd.read_pickle("ds.pkl"); d["lpay"]=np.log(d["plan_pay"])
t=d[d["date"]>="20260829"].copy()
t["pub"]=pd.to_datetime(t["published_at"],format="mixed",errors="coerce")
t["sub_ts"]=pd.to_datetime(t["submitted_at"],format="mixed",errors="coerce")
t["hp"]=(t["origin"]=="highpay_fill")
t["start_jst"]=pd.to_datetime(pd.to_numeric(t["start_at"],errors="coerce"),unit="s",errors="coerce")+pd.Timedelta(hours=9)
t["lead"]=((t["start_jst"]-t["pub"]).dt.total_seconds()/60).where(lambda x:x.between(0,1440))
t["pub_rank"]=t.groupby("date")["sub_ts"].rank(pct=True)
t["start_h"]=t["start_jst"].dt.hour+t["start_jst"].dt.minute/60
print("### G2 の腕の釣り合い（高額枠 vs 同型の通常商品・B/C/D・7車・09/06以降）")
w=t[(t["date"]>="20260906")&(t["n_entries"]==7)].copy()
w=w[w["plan"].str.match(r"^[BCD]_")]
for nm,s in [("通常 _hit",w[~w["hp"]]),("高額枠",w[w["hp"]])]:
    print(f"  {nm}: n={len(s)} paid={s['paid'].mean():.0f} 公開順位(pct)={s['pub_rank'].mean():.3f} "
          f"リード時間中央={s['lead'].median():.0f}分 発走時刻中央={s['start_h'].median():.1f}h "
          f"R番号={s['race_no'].mean():.1f} 決勝率={s['final'].mean():.3f} 自信あり={s['conf'].mean():.3f} "
          f"日目中央={s['day_index'].median()}")
A=w[w["hp"]]["paid"].values; B=w[~w["hp"]]["paid"].values
rng=np.random.default_rng(2)
bs=[rng.choice(A,len(A),True).mean()-rng.choice(B,len(B),True).mean() for _ in range(6000)]
print(f"  差 = {A.mean()-B.mean():+.0f}pt CI95 [{np.percentile(bs,2.5):.0f},{np.percentile(bs,97.5):.0f}]  (G報告 +646 [441,870])")
# 同日ペア（日FE相当）
pair=[]
for dt,s in w.groupby("date"):
    a=s[s["hp"]]["paid"]; b=s[~s["hp"]]["paid"]
    if len(a)>=1 and len(b)>=1: pair.append(a.mean()-b.mean())
print(f"  同日ペア差の平均 = {np.mean(pair):+.0f}pt  (n日={len(pair)}, 正の日 {sum(1 for x in pair if x>0)}/{len(pair)})")
print("\n### 公開順・時刻の交絡")
print(t.groupby("hp").agg(n=("paid","size"),pubrank=("pub_rank","mean"),lead=("lead","median"),
    starth=("start_h","median"),sub_h=("sub_ts",lambda x:x.dt.hour.median())).round(3).to_string())
print("\n### G3 自信あり: 同日同種別比 + 穴狙いアイコン統制")
tt=t[t["plan_pay"].notna()].copy(); tt["conf_f"]=tt["conf"].astype(float)
LONG={"A_ana","F_pay","T_upset"}|{f"{x}_{k}" for x in "ABCDEF" for k in ("sign","big")}
tt["long_f"]=(tt["plan"].isin(LONG)&~tt["conf"]).astype(float); tt["lpay"]=np.log(tt["plan_pay"])
for cols,lab in [(["conf_f"],"素"),(["conf_f","lpay"],"+計画払戻"),(["conf_f","long_f","lpay"],"+アイコン+払戻")]:
    X,names=dmat(tt,cols,["date","seg"]); res,_,_=ols(tt["lpaid"].values,X,names)
    print(f"  {lab:<16} conf {res['conf_f'][0]:+.3f} [{res['conf_f'][1]:+.3f},{res['conf_f'][2]:+.3f}]")
c=tt[tt["conf"]]; print(f"  自信あり n={len(c)} paid平均={c['paid'].mean():.0f} 計画払戻中央={c['plan_pay'].median():,.0f} プラン={c['plan'].value_counts().to_dict()}")
