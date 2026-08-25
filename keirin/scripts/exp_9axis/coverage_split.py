"""9車を**開催まるごと網羅**した前提で、当たるレース／外れるレースを切り分ける。

ゲートで落とす選択肢が無い（グレード開催は全レース穴埋め対象）ので、
問いは「どのレースを買うか」ではなく **「どのレースにどの買い方を当てるか」**。

探索窓 2024-07〜2025-12 / 確認窓 2026-01〜2026-08-04 の二段で見る。
"""
import sys, glob, pickle, numpy as np, pandas as pd
sys.path.insert(0,'.')
from src.database import get_connection
from src.strategy_wt import RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN, RANK_9C_P3_SUM_MIN
from src.p3_calibration import calibrated_p3_sum_top2

fr=[]
for f in sorted(glob.glob("data/exp_cache/wf_preds9_*.pkl")):
    d=pickle.load(open(f,"rb")); fr.append(d[["race_key","frame_no","pp3","ppw"]])
P=pd.concat(fr,ignore_index=True).drop_duplicates(["race_key","frame_no"])
keys=sorted(P.race_key.unique())
meta={}; ent={}; odds={}
with get_connection() as c:
    for i in range(0,len(keys),500):
        ch=keys[i:i+500]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,race_date,race_type,race_no,day_index,cup_grade,cup_id,n_entries,venue_id FROM wt_races WHERE race_key IN ({ph})",ch):
            meta[r["race_key"]]=dict(r)
        for r in c.execute(f"SELECT race_key,frame_no,finish_order,line_group,line_size,line_pos,is_line_leader,n_lines,style FROM wt_entries WHERE race_key IN ({ph})",ch):
            ent.setdefault(r["race_key"],[]).append(dict(r))
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds WHERE bet_type='trio' AND race_key IN ({ph})",ch):
            odds.setdefault(r["race_key"],{})[r["combination"]]=float(r["odds_value"])

rows=[]
for rk,g in P.groupby("race_key"):
    m=meta.get(rk); es=ent.get(rk)
    if not m or not es or m["n_entries"]!=9: continue
    fo={e["frame_no"]:e["finish_order"] for e in es}
    win={f for f,o in fo.items() if o and 1<=int(o)<=3}
    if len(win)!=3: continue
    p3=dict(zip(g.frame_no.astype(int),g.pp3.astype(float)))
    if len(p3)!=9: continue
    E={e["frame_no"]:e for e in es}
    order=sorted(p3,key=lambda f:(-p3[f],f))
    a1,a2=order[0],order[1]
    legs=[f for f in order[2:] if p3[f]>=RANK_9C_LEG_P3_MIN]
    if len(legs)<RANK_9C_LEGS_MIN:
        legs=order[2:2+RANK_9C_LEGS_MIN]          # 穴埋め経路は最低点数まで戻す
    grid=odds.get(rk) or {}
    st=10000/len(legs); hit=0; pay=0.0
    for L in legs:
        if {a1,a2,L}==win:
            hit=1; pay=st*grid.get("=".join(map(str,sorted((a1,a2,L)))),0.0)
    lg1=E[a1]["line_group"]; lg2=E[a2]["line_group"]
    sizes=sorted([e["line_size"] or 1 for e in es if (e["line_pos"] or 1)==1], reverse=True)
    rows.append(dict(rk=rk,date=str(m["race_date"]),rtype=m["race_type"],rno=m["race_no"],
        day=m["day_index"],cg=m["cup_grade"],cup=m["cup_id"],venue=m["venue_id"],
        hit=hit,pay=pay,n=len(legs),
        both=int(a1 in win and a2 in win), a1_in=int(a1 in win), a2_in=int(a2 in win),
        same=int(lg1 is not None and lg1==lg2),
        gate=float(calibrated_p3_sum_top2(p3,m["race_type"],m["cup_grade"]) or 0),
        p3sum=p3[a1]+p3[a2], nl=E[a1]["n_lines"] or 0,
        pat="-".join(map(str,sizes)) if sizes else "?",
        senko=sum(1 for e in es if e["style"]=="逃"),
        a1_lead=E[a1]["is_line_leader"] or 0, a1_size=E[a1]["line_size"] or 1,
        a2_lead=E[a2]["is_line_leader"] or 0))
D=pd.DataFrame(rows); D.to_pickle("/tmp/cov9.pkl")
D["win_"]=D.date<"2026-01-01"
print(f"9車 {len(D):,}R（探索 {D.win_.sum():,} / 確認 {(~D.win_).sum():,}）\n")

rng=np.random.default_rng(21)
def line(s,lab,pad=26):
    if len(s)<60: return f"  {lab:<{pad}} {len(s):5d}R  ← 件数不足"
    pay=s.pay.values; inv=10000*len(s)
    b=[pay[rng.integers(0,len(pay),len(pay))].sum()/inv*100 for _ in range(1500)]
    return (f"  {lab:<{pad}} {len(s):5d}R 二軸 {s.both.mean()*100:5.1f}% 的中 {s.hit.mean()*100:5.1f}% "
            f"ROI {pay.sum()/inv*100:6.1f}% CI[{np.percentile(b,2.5):3.0f},{np.percentile(b,97.5):3.0f}]")
def split(key, lab, minn=60, pad=26):
    print(f"■ {lab}")
    for v,s in D.groupby(key):
        a=s[s.win_]; b=s[~s.win_]
        if len(s)<minn: continue
        print(f"  {str(v):<{pad}} 全 {len(s):5d}R 的中 {s.hit.mean()*100:5.1f}% ROI {s.pay.sum()/(10000*len(s))*100:6.1f}%"
              f"  | 探索 {len(a):4d}R {a.hit.mean()*100 if len(a) else 0:5.1f}%/{a.pay.sum()/(10000*max(len(a),1))*100:5.1f}%"
              f"  | 確認 {len(b):4d}R {b.hit.mean()*100 if len(b) else 0:5.1f}%/{b.pay.sum()/(10000*max(len(b),1))*100:5.1f}%")
    print()

print(line(D,"【全網羅】9車 全レース"))
print(line(D[D.gate>=RANK_9C_P3_SUM_MIN],"うちゲート通過"))
print(line(D[D.gate< RANK_9C_P3_SUM_MIN],"うちゲート不通過(穴埋め)"))
print()
split("rtype","種別",minn=80,pad=10)
split("nl","ライン数",minn=100,pad=10)
split("same","二軸が同ラインか(1=同)",minn=100,pad=22)
split("day","開催何日目",minn=100,pad=10)
D["gband"]=pd.cut(D.gate,[0,1.15,1.25,1.30,1.40,1.55,3.0])
split("gband","ゲート値の帯",minn=100,pad=14)
D["rband"]=pd.cut(D.rno,[0,4,7,9,12],labels=["1-4R","5-7R","8-9R","10R+"])
split("rband","レース番号",minn=100,pad=10)
split("senko","先行人数",minn=100,pad=10)
split("pat","ライン構成",minn=150,pad=12)
