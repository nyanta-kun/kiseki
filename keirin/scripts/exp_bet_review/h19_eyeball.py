#!/usr/bin/env python3
"""H19 目視確認: k=3/2/1（と k=3 の捨て）各1レースで ①②③④ の買い目・合成オッズ・結果を出す。"""
import pickle, sys
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import h19_build as B

z = pickle.load(open(D / "h19" / "h19_arms_0.60.pkl", "rb"))
arms, cur, races = z["arms"], z["cur"], z["races"]
b = load_board_2025(); S._Z = {k: b[k] for k in S._NEED}


def show(tag, r):
    if r is None:
        print(f"   {tag}: 見送り/なし"); return
    st = sorted(r["stakes"].items(), key=lambda kv: -kv[1])
    print(f"   {tag}: {r['n']}点 投資{r['inv']:,.0f} 払戻{r['pay']:,.0f} 合成{r['comp']:.2f}倍 {r.get('plan','')} "
          + " ".join(f"{'-'.join(map(str,k))}:{v}" for k, v in st[:10]))


want = {("built", 3): 2, ("built", 2): 2, ("built", 1): 2, ("discard", 3): 2, ("fallback", 2): 1, ("fallback", 1): 1}
seen = {}
for key in sorted(arms):
    a = arms[key]
    if key not in cur:
        continue
    t = (a["kind"], a["k"])
    if t in want and seen.get(t, 0) < want[t]:
        # 的中したレースと外れたレースを1つずつ見る
        hit = (a["r2"] or a["r3"] or {}).get("pay", 0) > 0
        tag = (t, hit)
        if seen.get(tag, 0) >= 1:
            continue
        seen[tag] = 1; seen[t] = seen.get(t, 0) + 1
        i = races[key]["i"]; x = S.ctx(i)
        p3 = {c: float(b["P3"][i][c - 1]) for c in range(1, 8)}
        c = cur[key]
        print(f"\n== {key} k={a['k']} kind={a['kind']} 型{races[key]['tl']} {races[key]['rtype']}  p3={ {c_: round(v,2) for c_, v in p3.items()} } 堅い={a['firm']}  着={x.win_tf} 確定オッズ={x.pay_tf:.1f}倍")
        print(f"   ① {c['plan']} slot={c['slot']}: {c['n']}点 投資{c['inv']:,.0f} 払戻{c['pay']:,.0f} 合成{1/sum(1/x.po_tf[k_] for k_ in c['stakes']):.2f}倍 " + " ".join(f"{'-'.join(map(str,k_))}:{v}" for k_, v in sorted(c['stakes'].items(), key=lambda kv: -kv[1])[:10]))
        show("② ", a["r2"]); show("③ ", a["r3"]); show("④₂", a["c2"])
        if a["kind"] == "discard":
            print("   捨て理由:", a.get("discard_why"), " 穴目失敗:", a["ana_why"])
