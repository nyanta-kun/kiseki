#!/usr/bin/env python3
"""「軸2車が別ラインなら売らない」は、既存の軸信頼ゲートを同じ量だけ深くするのと
どちらが良いか（2026-09-10）。

🔴 無作為対照だけでは足りない。**同じ件数を落とす既存の道具**と比べないと
   「新しい情報が要る」ことにならない（`race_filter_2026_08_27` の教訓の一段上）。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path
from statistics import median

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
from board_pattern_product import HIT_PLANS, kpi, load, passes_axis_gate, show  # noqa


def main() -> None:
    rows, pat = load()
    rows = [r for r in rows if r["plan"] in HIT_PLANS and passes_axis_gate(r)]
    ax_same = pat["ax_same"].astype(bool)
    for w in ("explore", "confirm"):
        rr = [r for r in rows if r["win"] == w]
        drop_n = sum(1 for r in rr if not ax_same[r["_pi"]])
        frac = drop_n / len(rr)
        print(f"\n== {w} ==  母集団 {len(rr)}  落とす件数 {drop_n} ({100*frac:.1f}%)")
        show("現行", kpi(rr))
        show("① 別ライン軸を売らない",
             kpi([r for r in rr if ax_same[r["_pi"]]]))
        # ② 軸信頼ゲートを同じ量だけ深くする（プラン内の下位 frac を外す）
        keep = []
        for pl in {r["plan"] for r in rr}:
            g = [r for r in rr if r["plan"] == pl]
            thr = np.quantile([r["axis"] for r in g], frac)
            keep += [r for r in g if r["axis"] >= thr]
        show("② 軸信頼ゲートを同じ量だけ深く", kpi(keep))
        # ③ 両方の合わせ技（半分ずつ）
        half = frac / 2
        keep3 = []
        for pl in {r["plan"] for r in rr}:
            g = [r for r in rr if r["plan"] == pl and ax_same[r["_pi"]]]
            if not g:
                continue
            thr = np.quantile([r["axis"] for r in g], max(0.0, half))
            keep3 += [r for r in g if r["axis"] >= thr]
        show("③ 別ライン外し ＋ ゲートを少し深く", kpi(keep3))
        outs = []
        for s in range(20):
            rng = np.random.default_rng(s)
            pick = set(rng.choice(len(rr), size=drop_n, replace=False).tolist())
            outs.append(kpi([r for i, r in enumerate(rr) if i not in pick]))
        print(f"  {'無作為対照20seed 中央':<38} 表示的中 "
              f"{float(np.median([o['shown'] for o in outs])):.2f}%  "
              f"ROI {float(np.median([o['roi'] for o in outs])):.1f}")


if __name__ == "__main__":
    main()
