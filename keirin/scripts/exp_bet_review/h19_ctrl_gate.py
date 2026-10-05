#!/usr/bin/env python3
"""H19 補足: ④ は合成 2.2 倍・入稿ゲートを掛けていない。その割合を k 別に数える（再ビルド不要・ctx は台から組み直す）。"""
import pickle
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import src.type_lab as TL

z = pickle.load(open(D / "h19" / "h19_arms_0.60.pkl", "rb"))
arms, cur, races = z["arms"], z["cur"], z["races"]
b = load_board_2025(); S._Z = {k: b[k] for k in S._NEED}
cache = {}
rows = []
for key, a in arms.items():
    if key not in cur or a["kind"] != "built" or a["c2"] is None:
        continue
    x = S.ctx(races[key]["i"])
    for arm, r in (("②", a["r2"]), ("④₂", a["c2"])):
        st = r["stakes"]
        mean = TL.mean_expected_payout(st, x.po_tf)
        rows.append((a["k"], arm, r["comp"], bool(S.gate_ok(st, x.po_tf, mean))))
out = ["| k | 腕 | 組めたレース | 合成 < 2.2倍 | 本番ゲート落ち（平均想定払戻 ≤ 2万円 or 最低 < 2.0倍） |", "|---|---|---|---|---|"]
for k in (1, 2, 3, "全体"):
    for arm in ("②", "④₂"):
        r = [t for t in rows if t[1] == arm and (k == "全体" or t[0] == k)]
        n = len(r)
        out.append(f"| {k} | {arm} | {n:,} | {sum(t[2] < 2.2 for t in r)/n*100:.1f}%（{sum(t[2] < 2.2 for t in r):,}） | {sum(not t[3] for t in r)/n*100:.1f}%（{sum(not t[3] for t in r):,}） |")
print("\n".join(out))
open(D / "h19" / "h19_ctrl_gate.md", "w").write("\n".join(out))
