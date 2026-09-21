#!/usr/bin/env python3
"""Σp3 = 3.0 正規化が型ラボの**商品KPI**をどう動かすか（2026-09-21）。

発端: `docs/meeting_structure_unused_2026_09_21.md` §4。
`AXIS_SUM_FIRM = 1.44` は**生の p3** の上位2合計を絶対閾値で切るが、
レースごとの Σp3 は 3.0 に揃っておらず車数で系統的にずれている
（実測 5車 2.79 / 7車 3.03 / 9車 3.16）。

🔴 **型は「どのプランを売るか」しか決めない**（`shape.label` も `shape.firm` も
   `build_legs` は見ない・grep 0件）。したがって正規化の効果は
   **A↔D / B↔E / C↔F の入れ替わりだけ**に閉じる。

🔴 **件数が変わる比較なので、無作為対照を必ず置く**
   （[[keirin_type_lab_race_filter_rejected_2026_08_27]]:
   「件数を減らす検証には必ず無作為対照を置く」）。ここでは
   **正規化と同じ件数だけ無作為に firm を剥がす腕**を対照にする。

台は `/tmp/race_type_board.npz`（7車・vintage walk-forward）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for, _run_named  # noqa: E402
from src.type_lab import AXIS_SUM_FIRM, SIGNBOARD_RACE_TYPES  # noqa: E402

FIRM_MAP = {"A": "D", "B": "E", "C": "F", "D": "A", "E": "B", "F": "C"}
S_BUCKET = {"A": 0, "D": 0, "B": 1, "E": 1, "C": 2, "F": 2}


def keys_for(tl: str) -> list[str]:
    if tl == "F":
        return ["F_hit", "F_pay", "F_sign"]
    if tl == "A":
        return ["A_ana", "A_trio", "A_hit"]
    return [{"B": "B_hit", "C": "C_hit", "D": "D_hit", "E": "E_hit"}[tl]]


def main() -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = np.array([float(v) for v in z["AXIS_SUM"]])
    P3 = z["P3"].astype(float)
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)

    for label, win in (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm")):
        base = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        nd = C.days_of(C.select(None, win))
        recs, meta = {}, []
        for n, i in enumerate(base):
            if n % 4000 == 0:
                print(f"  {label} {n:,}/{len(base):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            raw = tp[i]
            p = np.sort(P3[i])[::-1]
            tot = P3[i].sum()
            axis_norm = (p[0] + p[1]) * 3.0 / tot if tot > 0 else axs[i]
            firm_norm = axis_norm >= AXIS_SUM_FIRM
            firm_raw = raw in "ABC"
            nrm = raw if firm_norm == firm_raw else FIRM_MAP[raw]
            need = set(keys_for(raw)) | set(keys_for(nrm))
            r = {k: _run_named(x, k) for k in need}
            recs[i] = r
            meta.append((i, raw, nrm, x.shape.pw_ent, float(axs[i]), float(axis_norm), firm_raw, firm_norm))

        def pick(tl: str, i: int, pw: float) -> dict | None:
            trio_ok = recs[i].get("A_trio") is not None if tl == "A" else False
            k = _plan_for(tl, rt[i], pw, trio_ok, sign_rt)
            return recs[i].get(k)

        def run(assign) -> list:
            out = []
            for (i, raw, nrm, pw, ar, an, fr, fn) in meta:
                tl = assign(raw, nrm, fr, fn, i)
                r = pick(tl, i, pw)
                if r:
                    out.append(r)
            return out

        arm_raw = run(lambda raw, nrm, fr, fn, i: raw)
        arm_norm = run(lambda raw, nrm, fr, fn, i: nrm)
        # 対照: 正規化と**同じ件数だけ**無作為に firm を剥がす／付ける
        flips_off = [m[0] for m in meta if m[6] and not m[7]]
        flips_on = [m[0] for m in meta if not m[6] and m[7]]
        firm_pool = [m[0] for m in meta if m[6]]
        soft_pool = [m[0] for m in meta if not m[6]]
        ctrl = []
        for seed in range(5):
            rng = np.random.default_rng(seed)
            off = set(rng.choice(firm_pool, min(len(flips_off), len(firm_pool)), replace=False))
            on = set(rng.choice(soft_pool, min(len(flips_on), len(soft_pool)), replace=False))
            ctrl.append(run(lambda raw, nrm, fr, fn, i: FIRM_MAP[raw] if (i in off or i in on) else raw))

        print("\n" + "=" * 122)
        print(f"=== {label}  n={len(meta):,}R  日数={nd}  "
              f"firm 剥がれ {len(flips_off):,} / 付き {len(flips_on):,} "
              f"（入れ替わり {(len(flips_off)+len(flips_on))/len(meta)*100:.1f}%）===")
        print(C.HEAD)
        print(C.line("① 現行（生 axis_sum）", C.summarize(arm_raw, nd)))
        print(C.line("② Σp3=3.0 正規化", C.summarize(arm_norm, nd)))
        for j, c in enumerate(ctrl):
            print(C.line(f"③ 対照 同数を無作為 seed{j}", C.summarize(c, nd)))


if __name__ == "__main__":
    main()
