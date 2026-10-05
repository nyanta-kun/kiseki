"""H01 目視2: LB で『買う目の集合』が変わったレースを cur/lb 並べて表示（A/B_hit のダッチ1件・F_hit の conf 1件）。"""
import pickle
from h01_step1 import *   # noqa
d = pickle.load(open(D / "h01_step1_recs.pkl", "rb"))
bc = {(r["race_key"], r["slot"]): r for r in d["cur"]["recs"]}; bl = {(r["race_key"], r["slot"]): r for r in d["lb"]["recs"]}
ch = sorted(k for k in set(bc) & set(bl) if set(bc[k]["stakes"]) != set(bl[k]["stakes"]))
pick = []
for pk in ("B_hit", "F_hit", "E_hit"):
    for k in ch:
        if bc[k]["plan"] == pk and len(bc[k]["stakes"]) <= 12:
            pick.append(k); break
z, idx = prep(); kidx = {str(z["KEY"][i]): i for i in idx}
for k in pick:
    x = make_ctx(kidx[k[0]], z)
    print("=" * 70); print(k, bc[k]["plan"], "LB events:", x.lb_events, "的中", x.win_tf)
    cs, ls = bc[k]["stakes"], bl[k]["stakes"]
    for c in sorted(set(cs) | set(ls), key=lambda c: x.po_tf[c]):
        tag = "cur のみ(押し出し)" if c not in ls else ("lb のみ(入った)" if c not in cs else "")
        print(f"  {c} 予測{x.po_tf[c]:>7.1f}  P_cur={x.pr_tf[c]:.4f} P_lb={x.pr_tf_lb[c]:.4f}  賭け cur={cs.get(c,0):>5} lb={ls.get(c,0):>5} {tag}")
    print(f"  採点 cur={bc[k]['inv']:.0f}/{bc[k]['pay']:.0f}  lb={bl[k]['inv']:.0f}/{bl[k]['pay']:.0f}  点数 {len(cs)}→{len(ls)}")
