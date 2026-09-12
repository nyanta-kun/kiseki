#!/usr/bin/env python3
"""B-5. 「bust_p で上位を落とす」は**既存の軸信頼ゲートを同じ量だけ深くしたの**と
違うのか（2026-09-11）。

`board_pattern_2026_09_10.md` が使ったのと同じ比較。無作為対照ではなく
**既存量で同数を落とした腕**を対照に置く。
"""
from __future__ import annotations
import sys, importlib.util
import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
mod = importlib.util.spec_from_file_location(
    "_b_lib", "/Users/ysuzuki/GitHub/kiseki/keirin/scripts/exp_type_lab/old_rank_ideas_B.py")
m = importlib.util.module_from_spec(mod)
mod.loader.exec_module(m)       # __name__ != "__main__" なので本体は走らない
B, kpi = m.B.copy(), m.kpi
S = m.SCORES
B["axis_sum"] = S.reindex(B.rk)["axis_sum"].values
B["rp_sd"] = S.reindex(B.rk)["rp_sd"].values
B["pw_gap12"] = S.reindex(B.rk)["pw_gap12"].values
B["arare"] = S.reindex(B.rk)["arare"].values

CAND = {"bust_p": False, "upset_p": False, "pw_ent": False,
        "axis_sum": True, "rp_sd": True, "pw_gap12": True, "arare": False}
#  値 True = **小さい方**が「軸が弱い」＝落とす側

for frac in (0.10, 0.20):
    print(f"\n## 同じ {int(frac*100)}% を落とす（落とす基準だけを替える）")
    for w in ("探索", "確認"):
        d = B[B.win == w].copy()
        d = d[d.bust_p.notna()]        # 全腕を同じ母集団で比べる
        nd = d.d.nunique()
        k = int(round(len(d) * frac))
        base = kpi(d.pay.values, d.bud.values, nd)
        print(f"[{w}] n={len(d):,}  落とす {k}件")
        print(f"   {'絞らない':16s} 件/日={base['per']:5.2f} 表示的中={base['shown']:5.2f}% "
              f"ROI={base['roi']:5.1f}% 10万+/日={base['hp']:.3f}")
        rows = []
        for c, asc in CAND.items():
            cut = set(d.sort_values(c, ascending=asc).head(k).rk)
            keep = d[~d.rk.isin(cut)]
            a = kpi(keep.pay.values, keep.bud.values, nd)
            rows.append((c, a))
        rng = np.random.default_rng(7)
        ctrl = []
        for _ in range(20):
            cc = set(rng.choice(d.rk.values, size=k, replace=False))
            kk = d[~d.rk.isin(cc)]
            ctrl.append(kpi(kk.pay.values, kk.bud.values, nd))
        cv = np.array([c["shown"] for c in ctrl])
        for c, a in sorted(rows, key=lambda x: -x[1]["shown"]):
            print(f"   {c:16s} 件/日={a['per']:5.2f} 表示的中={a['shown']:5.2f}% "
                  f"ROI={a['roi']:5.1f}% 中央={int(a['med']):6d} 10万+/日={a['hp']:.3f} "
                  f"無作為対照に {int((a['shown'] > cv).sum())}/20")
        print(f"   {'無作為(中央)':16s} 表示的中={np.median(cv):5.2f}% "
              f"ROI={np.median([c['roi'] for c in ctrl]):5.1f}% "
              f"10万+/日={np.median([c['hp'] for c in ctrl]):.3f}")
