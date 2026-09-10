#!/usr/bin/env python3
"""第4章 ライン隣接ボーナスの較正（2026-09-10）。

現行 λ=2.0 は「隣接そのもの」を強めるのと「向き」を決めるのを同時にやっている。
どちらがずれているのかを分けて測る。
"""
from __future__ import annotations
import sys, pickle
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C  # noqa: E402
from line_order_arms import build_ctx, variant_probs  # noqa: E402
from line_order_build import load_rates, CARS  # noqa: E402

def main():
    z = C.board()
    a = {k: z[k] for k in ("P3","PW","LG","A_line_pos","ST","A_race_point","BEHIND",
                           "DAYI","PO","TRIO_PO","TRIO_ODDS","TRIO_WIN","TRIO_PAY",
                           "WIN","PAY","DATE","TYPE","RTYPE","KEY","AXIS_SUM")}
    rates = load_rates(); tp = np.array([str(v) for v in a["TYPE"]])
    for win in ("explore","confirm"):
        idx=[int(i) for i in C.select(None,win) if tp[int(i)] in "ABCDEF"]
        n=0; e_same=e_fwd=0.0; a_same=a_fwd=0
        e_nolm=0.0
        # 別ライン決着の内訳
        x_lead2=x_n=0
        for k,i in enumerate(idx):
            if k%5000==0: print(f"  {win} {k:,}/{len(idx):,}",flush=True)
            x=build_ctx(a,i,rates)
            if x is None or x.shape.type_label!=tp[i]: continue
            pr=variant_probs(x.p0,x.lg,x.lp,2.0,1.5,1.0,1.0)
            lmap={c:gi for gi,ln in enumerate(x.shape.lines) for c in ln}
            posin={c:j for ln in x.shape.lines for j,c in enumerate(ln)}
            same=fwd=0.0
            for (p,q,r),v in pr.items():
                if lmap.get(p) is not None and lmap.get(p)==lmap.get(q):
                    same+=v
                    if posin[p]<posin[q]: fwd+=v
            s0=sum(v for (p,q,r),v in x.p0.items()
                   if lmap.get(p) is not None and lmap.get(p)==lmap.get(q))
            n+=1; e_same+=same; e_fwd+=fwd; e_nolm+=s0
            w=x.win_tf
            if lmap.get(w[0]) is not None and lmap.get(w[0])==lmap.get(w[1]):
                a_same+=1
                if posin[w[0]]<posin[w[1]]: a_fwd+=1
            else:
                x_n+=1
                x_lead2+=int(posin.get(w[1],99)==0 or lmap.get(w[1]) is None)
        print(f"\n== {win}  n={n}")
        print(f"  P(1-2着が同ライン)   モデル {100*e_same/n:6.2f}%  ボーナス無し {100*e_nolm/n:6.2f}%"
              f"  実測 {100*a_same/n:6.2f}%   過大 {100*(e_same-a_same)/n:+6.2f}pt")
        print(f"  P(隊列順 | 同ライン)  モデル {100*e_fwd/e_same:6.2f}%"
              f"  実測 {100*a_fwd/a_same:6.2f}%   過大 {100*(e_fwd/e_same-a_fwd/a_same):+6.2f}pt")
        print(f"  1-2着が別ラインのとき 2着が「別ラインの先頭 or 単騎」 {100*x_lead2/x_n:6.2f}% (n={x_n})")

if __name__=="__main__":
    main()
