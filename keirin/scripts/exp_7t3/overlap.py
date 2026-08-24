"""7T1（本番定義）と 決勝×30倍+×5点 の重なりを実測する。"""
import pickle, sys, numpy as np, pandas as pd
sys.path.insert(0,'.'); sys.path.insert(0,'/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/7bc12223-92ab-4766-81d1-18bc01d1b207/scratchpad')
from src.strategy_wt import (rank_7t1_select, rank_7t1_daily_select, rank_7t1_stakes,
    rank_7t1_is_target_race_type, rank_7t1_expected_value, rank_7t1_pl_prob)
from src.marquee import MARQUEE_KEYWORDS
from tfprob import blend_pl
print("MARQUEE_KEYWORDS =", MARQUEE_KEYWORDS)
for k in (1,2,3,5):
    from src.strategy_wt import rank_7t1_min_stake, RANK_7T1_TARGET_PAYOUT, RANK_7T1_BUDGET, RANK_7T1_UNIT
    s=rank_7t1_min_stake(k); print(f"  7T1 {k}点: 1点 {s:,}円 → 必要オッズ {RANK_7T1_TARGET_PAYOUT/s:,.0f}倍以上")

BOARD=pickle.load(open("/tmp/keirin_tf_board.pkl","rb"))
d=pickle.load(open("/tmp/keirin_upset_ds.pkl","rb"))
E=d["E"].merge(d["pred"],on=["race_key","frame_no"],how="inner")
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key")
E=E[E.race_key.isin(set(BOARD))]
CANON=list(BOARD[list(BOARD)[0]][0])
recs=[]
for rk,g in E.groupby("race_key"):
    if len(g)!=7: continue
    rt=str(F.race_type.get(rk,"")); date=F.race_date.get(rk,"")
    cars=g.frame_no.astype(int).tolist()
    p3=dict(zip(cars,g.pp3.astype(float))); pw=dict(zip(cars,g.ppw.astype(float)))
    po=dict(zip(CANON,np.asarray(BOARD[rk][1],float)))
    order=sorted(cars,key=lambda c:-p3[c])
    lg=dict(zip(cars,g.line_group))
    cross = not (lg.get(order[0]) is not None and lg.get(order[0])==lg.get(order[1]))
    # 7T1
    sel=rank_7t1_select(p3,pw,po)
    t1_legs = [tuple(int(x) for x in s.split("-")) for s in sel[2]] if sel else []
    ev=None
    if sel:
        st=rank_7t1_stakes(sel[2]); tot=sum(st.values())
        ev=sum((rank_7t1_pl_prob(pw,l) or 0)*po.get(tuple(int(x) for x in l.split("-")),0)*st[l]
               for l in sel[2])/tot if tot else None
    t1_ok = (rank_7t1_is_target_race_type(rt) and cross and bool(t1_legs))
    # 新案
    P=blend_pl(cars,pw,p3,(1,.5,0))
    band={k2:v for k2,v in po.items() if v>=30}
    new_legs=sorted(band,key=lambda k2:-P.get(k2,0))[:5] if band else []
    new_ok = rt in ("決勝","チャレンジ決勝") and bool(new_legs)
    recs.append(dict(race_key=rk,date=str(date),rtype=rt,cross=cross,
        t1=t1_ok,t1_n=len(t1_legs) if t1_ok else 0,t1_ev=ev,
        t1_legs=t1_legs if t1_ok else [], new=new_ok,new_legs=new_legs if new_ok else []))
D=pd.DataFrame(recs)
# 7T1 の日次上限5（ev降順）
D["t1_final"]=False
for day,g in D[D.t1].groupby("date"):
    idx=g.sort_values("t1_ev",ascending=False).head(5).index
    D.loc[idx,"t1_final"]=True
D.to_pickle("/tmp/overlap.pkl")
nd=D.date.nunique()
print(f"\n対象 {len(D):,}R / {nd}日")
print(f"7T1（母集団条件のみ）      : {int(D.t1.sum()):,}R  {D.t1.sum()/nd:.1f}件/日")
print(f"7T1（日次上限5適用＝実入稿）: {int(D.t1_final.sum()):,}R  {D.t1_final.sum()/nd:.1f}件/日  平均{D[D.t1_final].t1_n.mean():.1f}点")
print(f"新案（決勝×30倍+×5点）     : {int(D.new.sum()):,}R  {D.new.sum()/nd:.1f}件/日")
both=D.t1_final&D.new
print(f"\n重なり（両方が取るレース）  : {int(both.sum()):,}R  {both.sum()/nd:.2f}件/日")
print(f"  新案のうち 7T1 と重なる割合 : {both.sum()/max(D.new.sum(),1)*100:.1f}%")
print(f"  7T1 のうち 新案と重なる割合 : {both.sum()/max(D.t1_final.sum(),1)*100:.1f}%")
ov=D[both]
same=sum(len(set(r.t1_legs)&set(r.new_legs)) for r in ov.itertuples())
print(f"  重なったレースでの買い目の一致点数 : 合計 {same} 点（7T1 {ov.t1_n.sum()}点 / 新案 {len(ov)*5}点）")
print("\n【母集団条件の違い】")
print(f"  7T1 の種別内訳 : {D[D.t1_final].rtype.value_counts().head(8).to_dict()}")
print(f"  新案の種別内訳 : {D[D.new].rtype.value_counts().to_dict()}")
print(f"  新案のうち別ライン(cross)の割合 : {D[D.new].cross.mean()*100:.1f}%")
