"""三連単 万車券枠: レース選別 × 買い目5点 の設計と評価（1本化・vectorized）。"""
import pickle, sys, itertools, time
import numpy as np, pandas as pd
sys.path.insert(0,'.')

t0=time.time()
BOARD = pickle.load(open("/tmp/keirin_tf_board.pkl","rb"))
print(f"board {len(BOARD):,}  {time.time()-t0:.0f}s", flush=True)

# combos の並びが全レース共通か確認（build_race_features は cars をソートするので 1..7 固定）
ks = list(BOARD)[:50]
CANON = list(BOARD[ks[0]][0])
assert all(list(BOARD[k][0])==CANON for k in ks), "combos の並びがレースで違う"
CIDX = {c:i for i,c in enumerate(CANON)}
PERM = np.array(CANON) - 1          # 0-indexed 車番 (210,3)
print("combos:", len(CANON), CANON[:3])

d = pickle.load(open("/tmp/keirin_upset_ds.pkl","rb"))
E = d["E"].merge(d["pred"], on=["race_key","frame_no"], how="inner")
F0 = pickle.load(open("/tmp/keirin_upset_frame.pkl","rb"))
FEAT = pickle.load(open("/tmp/keirin_feat.pkl","rb")).set_index("race_key")
E = E[E.race_key.isin(set(BOARD))].sort_values(["race_key","frame_no"])
fin = E[E.finish_order.notna()].copy(); fin["fo"]=fin.finish_order.astype(int)
t3=(fin[fin.fo.isin([1,2,3])].sort_values(["race_key","fo"]).groupby("race_key").frame_no.apply(tuple))
ACT = {k:tuple(int(x) for x in v) for k,v in t3[t3.apply(len)==3].items()}

tfo = pickle.load(open("/tmp/keirin_tf_odds.pkl","rb"))
tfo["key"]=tfo.combination.str.replace("=","-").str.split("-").apply(lambda v:tuple(int(x) for x in v))
ACTODDS = {rk: float(o) for rk,k,o in zip(tfo.race_key,tfo.key,tfo.odds_value) if ACT.get(rk)==k}
REALFULL = {rk: g.set_index("key").odds_value.to_dict() for rk,g in tfo.groupby("race_key")} \
           if False else None
print(f"act {len(ACT):,} / actodds {len(ACTODDS):,}  {time.time()-t0:.0f}s", flush=True)
del tfo

# 車番ごとの ppw / pp3 を (n_race, 7) 行列に
ppw = E.pivot(index="race_key", columns="frame_no", values="ppw")
pp3 = E.pivot(index="race_key", columns="frame_no", values="pp3")
ppw = ppw.reindex(columns=[1,2,3,4,5,6,7]); pp3 = pp3.reindex(columns=[1,2,3,4,5,6,7])
ok = ppw.notna().all(1) & pp3.notna().all(1)
ppw, pp3 = ppw[ok], pp3[ok]
RK = [k for k in ppw.index if k in BOARD and k in ACT and k in ACTODDS]
ppw = ppw.loc[RK].values; pp3 = pp3.loc[RK].values
print(f"races {len(RK):,}  {time.time()-t0:.0f}s", flush=True)

A = ppw/ppw.sum(1,keepdims=True); B = pp3/pp3.sum(1,keepdims=True)
S = [A, np.sqrt(A*B), B]
S = [s/s.sum(1,keepdims=True) for s in S]
i0,i1,i2 = PERM[:,0],PERM[:,1],PERM[:,2]
s0 = S[0][:,i0]
d1 = 1.0 - S[1][:,i0]; s1 = S[1][:,i1]/d1
d2 = 1.0 - S[2][:,i0] - S[2][:,i1]; s2 = S[2][:,i2]/np.clip(d2,1e-9,None)
PROB = s0*s1*s2
PROB /= PROB.sum(1,keepdims=True)
PO = np.vstack([np.asarray(BOARD[k][1],float) for k in RK])
ACTI = np.array([CIDX[ACT[k]] for k in RK])
AO  = np.array([ACTODDS[k] for k in RK])
DATE = np.array([FEAT.race_date.get(k,"") for k in RK])
P1ENT = np.array([FEAT.p1_ent.get(k,np.nan) for k in RK])
TARGET_SUM = 1.3364470863032172
BOARD10K = ((1.0/PO)*(PO>=100)).sum(1)/TARGET_SUM
N100 = (PO>=100).sum(1)
print(f"matrices ready {time.time()-t0:.0f}s", flush=True)
np.savez_compressed("/tmp/design_mat.npz", PROB=PROB.astype(np.float32), PO=PO.astype(np.float32),
    ACTI=ACTI, AO=AO, DATE=DATE, P1ENT=P1ENT, BOARD10K=BOARD10K, N100=N100,
    RK=np.array(RK), PERM=PERM)
print("saved /tmp/design_mat.npz", time.time()-t0)
