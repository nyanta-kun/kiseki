"""7T1（本番の賭け金配分）と新案の直接対決。"""
import pickle, sys, numpy as np, pandas as pd
sys.path.insert(0,'.')
from src.strategy_wt import rank_7t1_stakes
D=pd.read_pickle("/tmp/overlap.pkl")
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
RK=Z["RK"].astype(str); ACTI=Z["ACTI"]; AO=Z["AO"].astype(float); PERM=Z["PERM"]
act={rk:tuple(PERM[i]+1) for rk,i in zip(RK,ACTI)}
ao={rk:o for rk,o in zip(RK,AO)}
D=D[D.race_key.isin(act)].copy()
D["act"]=D.race_key.map(act); D["ao"]=D.race_key.map(ao)
nd=D.date.nunique()

def eval7t1(sub):
    inv=ret=0.0; hits=0; pays=[]
    for r in sub.itertuples():
        legs=["-".join(map(str,t)) for t in r.t1_legs]
        st=rank_7t1_stakes(legs); inv+=sum(st.values())
        a="-".join(map(str,r.act))
        if a in st:
            hits+=1; p=r.ao*st[a]; ret+=p; pays.append(p)
    return len(sub),hits,inv,ret,pays
def evalnew(sub,unit=2000):
    inv=ret=0.0; hits=0; pays=[]
    for r in sub.itertuples():
        inv+=unit*len(r.new_legs)
        if r.act in set(r.new_legs):
            hits+=1; p=r.ao*unit; ret+=p; pays.append(p)
    return len(sub),hits,inv,ret,pays
def show(lbl,R,h,inv,ret,pays):
    print(f"  {lbl:<28} {R:5d}R  的中 {h:4d}件({h/max(R,1)*100:5.2f}%)  ROI {ret/inv*100:5.1f}%  "
          f"払戻中央 {int(np.median(pays)) if pays else 0:>9,}円  最大 {int(max(pays)) if pays else 0:>9,}円")

print(f"■ それぞれの実入稿（1レース1万円ベース・{nd}日）")
show("7T1（上限5適用）", *eval7t1(D[D.t1_final]))
show("新案（決勝×30倍+×5点）", *evalnew(D[D.new]))
print(f"\n■ 重なった {int((D.t1_final&D.new).sum())}R での直接対決")
ov=D[D.t1_final&D.new]
show("7T1", *eval7t1(ov)); show("新案", *evalnew(ov))
print(f"\n■ 決勝(決勝/チャレンジ決勝) {int(D.new.sum())}R 全体で見ると")
k=D[D.new]
show("新案（全決勝を取る）", *evalnew(k))
show("7T1（うち取れているのは一部）", *eval7t1(k[k.t1_final]))
print(f"\n■ 7T1 が取っている非決勝 {int((D.t1_final&~D.new).sum())}R")
show("7T1（非決勝ぶん）", *eval7t1(D[D.t1_final&~D.new]))
print(f"\n7T1 の母集団条件を満たす決勝: {int((D.t1&D.new).sum()):,}R / 決勝全体 {int(D.new.sum()):,}R "
      f"= {(D.t1&D.new).sum()/D.new.sum()*100:.1f}%（別ライン制約）")
print(f"そのうち日次上限5を通過: {int((D.t1_final&D.new).sum()):,}R = {(D.t1_final&D.new).sum()/max((D.t1&D.new).sum(),1)*100:.1f}%")
