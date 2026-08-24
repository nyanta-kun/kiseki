"""7T1 の絞り方を『日次上限5』から『決勝で絞る』へ替えたらどうなるか。"""
import sys, numpy as np, pandas as pd
sys.path.insert(0,'.')
from src.strategy_wt import rank_7t1_stakes
rng=np.random.default_rng(404)
D=pd.read_pickle("/tmp/overlap.pkl")
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
RK=Z["RK"].astype(str); ACTI=Z["ACTI"]; AO=Z["AO"].astype(float); PERM=Z["PERM"]
act={rk:"-".join(map(str,PERM[i]+1)) for rk,i in zip(RK,ACTI)}
ao={rk:o for rk,o in zip(RK,AO)}
D=D[D.race_key.isin(act)].copy()
D["acts"]=D.race_key.map(act); D["ao"]=D.race_key.map(ao)
D["ymd"]=D.date; nd=D.ymd.nunique()
KESSHO={"決勝","チャレンジ決勝"}
JUN={"準決勝","チャレンジ準決勝"}

def score(sub,label):
    inv=ret=0.0; hits=0; pays=[]
    for r in sub.itertuples():
        legs=["-".join(map(str,t)) for t in r.t1_legs]
        if not legs: continue
        st=rank_7t1_stakes(legs); inv+=sum(st.values())
        if r.acts in st:
            hits+=1; p=r.ao*st[r.acts]; ret+=p; pays.append(p)
    R=len(sub)
    per=np.zeros(R); 
    b=[np.array(pays+[0]*(R-len(pays)))[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1500)] if inv else [0]
    print(f"  {label:<34} {R:5d}R {R/nd:5.2f}件/日  平均{sub.t1_n.mean():.1f}点  的中 {hits:3d}件({hits/max(R,1)*100:5.2f}%)  "
          f"ROI {ret/inv*100:6.1f}% CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}]  "
          f"中央 {int(np.median(pays)) if pays else 0:>8,}円  最大 {int(max(pays)) if pays else 0:>9,}円")

base=D[D.t1]           # 7T1 の母集団条件（決勝系×別ライン×legsあり）
print(f"7T1 母集団条件を満たすレース: {len(base):,}R = {len(base)/nd:.1f}件/日")
print("\n■ 絞り方の比較（買い目・賭け金は 7T1 の本番ロジックのまま）")
score(D[D.t1_final], "現行: 日次上限5（ev降順）")
score(base, "上限なし（母集団まるごと）")
score(base[base.rtype.isin(KESSHO)], "決勝のみ（決勝+チャレンジ決勝）")
score(base[base.rtype.isin(KESSHO|JUN)], "決勝+準決勝")
kes=base[base.rtype.isin(KESSHO)]
print("\n■ 別ライン制約を外したら（決勝のみ）")
allk=D[D.rtype.isin(KESSHO)&(D.t1_n>0)]
score(allk, "決勝のみ・別ライン制約なし")
allk2=D[D.rtype.isin(KESSHO)]
print(f"   （うち 7T1 の買い目が組めたのは {int((D.rtype.isin(KESSHO)&(D.t1_n>0)).sum())}/{int(D.rtype.isin(KESSHO).sum())}R）")
print("\n■ 決勝のみ＋日次上限を併用した場合")
for cap in [3,4,5]:
    kk=kes.copy(); keep=[]
    for day,g in kk.groupby("ymd"):
        keep+=list(g.sort_values("t1_ev",ascending=False).head(cap).index)
    score(kes.loc[keep], f"決勝のみ × 上限{cap}")
