#!/usr/bin/env python3
"""盤面パターンで**レースを選別**したら商品KPIが動くか（2026-09-10）。

🔴 `race_filter_2026_08_27.md` の教訓に従い、**件数を減らす腕には必ず無作為対照 20 seed
   （中央値）を置く**。あちらでは「無作為半分の対照が、型・種別・場で絞った全案より高い」
   という結果が出ている＝件数減による CI 拡大を効果と誤読しない。

台: /tmp/miss_anatomy_rows.pkl（1レース1売り商品・本番の `sell_plans_for`/`build_legs`/
    `allocate`・入稿ゲート通過後）× /tmp/board_pattern.pkl（盤面パターン）
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

#: 当てにいく商品だけ（一撃 `A_ana` / `F_sign` は KPI が別・DESIGN 2.1）
HIT_PLANS = {"A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"}

#: 🔴 **本番の軸信頼ゲート**（`backend/src/services/keirin_type_lab_gate.py` が正本）。
#:    `miss_anatomy_rows.pkl` は入稿ゲート2つしか通していないので、ここで掛ける。
#:    これを掛けないと「盤面パターンで絞る」腕が **既にゲートがやっている仕事**を
#:    もう一度やって勝ったように見える。
AXIS_GATE_MIN = {"A_hit": 1.537, "D_hit": 1.263, "E_hit": 1.245, "F_hit": 1.230}


def passes_axis_gate(r) -> bool:
    return float(r["axis"]) >= AXIS_GATE_MIN.get(r["plan"], 0.0)


def load():
    rows = pickle.load(open("/tmp/miss_anatomy_rows.pkl", "rb"))
    pat = pickle.load(open("/tmp/board_pattern.pkl", "rb"))
    idx = {k: i for i, k in enumerate(pat["key"].tolist())}
    for r in rows:
        i = idx.get(r["race_key"])
        r["_pi"] = i
    return [r for r in rows if r["_pi"] is not None], pat


def kpi(rows) -> dict:
    if not rows:
        return {}
    days = len({r["date"] for r in rows})
    pays = [r["pay"] for r in rows]
    sh = [r for r in rows if r["shown"]]
    return dict(n=len(rows), per_day=len(rows) / days,
                shown=100 * len(sh) / len(rows),
                hit=100 * sum(r["hit"] for r in rows) / len(rows),
                roi=100 * sum(pays) / sum(r["inv"] for r in rows),
                med=(median([r["pay"] for r in rows if r["hit"]])
                     if any(r["hit"] for r in rows) else 0.0),
                p100k=sum(1 for p in pays if p >= 100_000) / days)


def show(name, k):
    print(f"  {name:<34} 件/日 {k['per_day']:>5.2f}  表示的中 {k['shown']:>5.2f}%"
          f"  ROI {k['roi']:>5.1f}  払戻中央 {k['med']:>7.0f}"
          f"  10万+/日 {k['p100k']:.3f}  n={k['n']}")


def run(flag_name: str, flag: np.ndarray) -> None:
    rows, pat = load()
    rows = [r for r in rows if r["plan"] in HIT_PLANS and passes_axis_gate(r)]
    for w in ("explore", "confirm"):
        rr = [r for r in rows if r["win"] == w]
        keep = [r for r in rr if not flag[r["_pi"]]]
        drop = [r for r in rr if flag[r["_pi"]]]
        print(f"\n== {w} ==  母集団 {len(rr)}  除外 {len(drop)}"
              f" ({100*len(drop)/max(len(rr),1):.1f}%)")
        show("現行（全部売る）", kpi(rr))
        show(f"{flag_name} を売らない", kpi(keep))
        if drop:
            show(f"（除外された分だけ）", kpi(drop))
        # 無作為対照 20 seed・同数
        outs = []
        for s in range(20):
            rng = np.random.default_rng(s)
            pick = set(rng.choice(len(rr), size=len(drop), replace=False).tolist())
            outs.append(kpi([r for i, r in enumerate(rr) if i not in pick]))
        med_sh = float(np.median([o["shown"] for o in outs]))
        med_roi = float(np.median([o["roi"] for o in outs]))
        wins = sum(1 for o in outs if kpi(keep)["shown"] > o["shown"])
        print(f"  {'無作為対照20seed 中央':<34} 表示的中 {med_sh:>5.2f}%"
              f"  ROI {med_roi:>5.1f}   → 提案の勝ち {wins}/20")


if __name__ == "__main__":
    pat = pickle.load(open("/tmp/board_pattern.pkl", "rb"))
    which = sys.argv[1] if len(sys.argv) > 1 else "n35"
    if which == "n35":
        run("n35_in_axisline==3", pat["n35_in_axisline"] == 3)
    elif which == "axdiff":
        run("軸2車が別ライン", ~pat["ax_same"].astype(bool))
    elif which == "both":
        run("別ライン ∧ n35>=2",
            (~pat["ax_same"].astype(bool)) & (pat["n35_in_axisline"] >= 2))
