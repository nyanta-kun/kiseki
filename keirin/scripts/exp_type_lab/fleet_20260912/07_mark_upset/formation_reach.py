#!/usr/bin/env python3
"""C 補足: 「1着=◎○以外・2着=◎○」型の目が、現行の入稿ゲート（Σ(1/予測)<0.5）の中で
どこまで届くかの**到達点の測定**（商品設計ではない・設計は別エージェント）。
"""
import pickle, sys, itertools
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin"); sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin/scripts/exp_type_lab")
import common as C
HERE = Path(__file__).resolve().parent
E = pickle.load((HERE / "event_table.pkl").open("rb"))
P = pd.DataFrame(pickle.load((HERE / "product.pkl").open("rb")))[["i", "gate", "axis_ok", "key", "pay", "inv"]]
D = E.merge(P, on="i"); D["y23"] = D.y & D.mk23
z = C.board(); PO = z["PO"]; PW = z["PW"]; CIDX = C.CIDX
def forms(i, hon, tai, kfirst):
    pw = PW[i]; order = list(np.argsort(-pw) + 1)
    firsts = [c for c in order if c not in (hon, tai)][:kfirst]
    legs = [(a, b, c) for a in firsts for b in (hon, tai) for c in range(1, 8) if c not in (a, b)]
    return legs
rows = []
for win in ("explore", "confirm"):
    S = D[(D.win == win) & D.gate & D.axis_ok]
    for kf in (1, 2, 3):
        cov = hit_all = 0; sig = []; n23 = 0; pays = []
        for r in S.itertuples():
            legs = forms(r.i, r.hon, r.tai, kf)
            po = [PO[r.i][CIDX[c]] for c in legs]
            if any(not np.isfinite(x) or x <= 0 for x in po):
                continue
            s = sum(1 / x for x in po); sig.append(s)
            inl = r.fin in set(legs)
            if r.y23:
                n23 += 1; cov += inl
            hit_all += inl
        sig = np.array(sig)
        print(f"{win}: 1着=pw上位{kf}車(◎○除く)×2着=◎○×3着流し {kf*2*5}点  y∧mk23カバー {cov/n23*100:.1f}%  全レース的中 {hit_all/len(S)*100:.1f}%  "
              f"Σ(1/予測) 中央 {np.median(sig):.3f}  Σ<0.5(ゲート通過) {(sig<0.5).mean()*100:.1f}%  Σ<1.0 {(sig<1.0).mean()*100:.1f}%")
