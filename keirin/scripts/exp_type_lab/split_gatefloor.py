#!/usr/bin/env python3
"""第10章 「型の下端」で捨てているレースは、別の型の商品なら仕事になるか（2026-09-10）。

現行は 2段構えになっている:
  ① `race_shape` が 2×3 の格子（力関係 × 荒れやすさ）で型を決める
  ② `passes_axis_gate` が **4プラン（A_hit/D_hit/E_hit/F_hit）の下位1/5** を捨てる

②で捨てているのは「その型の商品の前提（力関係が堅い）が崩れている下端」。
本章は **同じレースを他の型の商品で売ったらどうなるか**を**対レース**で比べる
（母集団が完全に同じなので無作為対照は要らない。差は対応ブートストラップの95%CI）。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.type_lab import sell_plans_for  # noqa: E402
from scripts.exp_type_lab.split_common import GATE, gate_ok  # noqa: E402
from scripts.exp_type_lab.split_boundary import CONFIRM, EXPLORE, load_all  # noqa: E402


def sold_plan(by, meta, rk):
    """本番が実際に売る1商品（`sell_plans_for`）。無ければ None。"""
    m = meta[rk]
    trio = by[rk].get("A_trio")
    keys = [p.key for p in sell_plans_for(m["tl"], 7, m["rtype"], pw_ent=m["pw_ent"],
                                          trio_ok=bool(trio) and gate_ok(trio))]
    return keys[0] if len(keys) == 1 else None


def paired(rows_a, rows_b, iters=1000, seed=0):
    """同じレース集合で商品Aと商品Bを比べる（対応あり）。"""
    ia = np.array([r[0] for r in rows_a]); pa = np.array([r[1] for r in rows_a])
    ib = np.array([r[0] for r in rows_b]); pb = np.array([r[1] for r in rows_b])
    rng = np.random.default_rng(seed)
    n = len(ia)
    ds, dr = [], []
    for _ in range(iters):
        j = rng.integers(0, n, n)
        ds.append((pa[j] >= ia[j]).mean() * 100 - (pb[j] >= ib[j]).mean() * 100)
        dr.append(pa[j].sum() / ia[j].sum() * 100 - pb[j].sum() / ib[j].sum() * 100)
    return (float(np.mean(ds)), float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5)),
            float(np.mean(dr)), float(np.percentile(dr, 2.5)), float(np.percentile(dr, 97.5)))


def kpi(rows):
    inv = np.array([r[0] for r in rows]); pay = np.array([r[1] for r in rows])
    sh = pay >= inv
    return dict(n=len(rows), shown=sh.mean() * 100, roi=pay.sum() / inv.sum() * 100,
                big=(pay >= 100_000).mean() * 100,
                med=float(np.median(pay[sh])) if sh.any() else 0.0)


def main():
    by, meta = load_all()
    # 型ごとの「捨てている下端」と、代わりに当てうる商品
    CASES = [
        ("A", "A_hit", ("A_trio", "D_hit", "E_hit", "F_hit", "C_hit", "B_hit")),
        ("D", "D_hit", ("E_hit", "F_hit", "A_trio", "C_hit")),
        ("E", "E_hit", ("D_hit", "F_hit", "A_trio", "C_hit")),
        ("F", "F_hit", ("D_hit", "E_hit", "A_trio", "C_hit")),
    ]
    for lab, win in (("探索 2025", EXPLORE), ("確認 2026-01〜08", CONFIRM)):
        lo, hi = win
        print("\n" + "=" * 118)
        print(f"■ {lab} — 軸信頼ゲートで捨てている「型の下端」を別商品で売ったら")
        print("=" * 118)
        for tl, plan, alts in CASES:
            floor = GATE.AXIS_GATE_MIN[plan]
            sel = [rk for rk, m in meta.items()
                   if lo <= m["date"] <= hi and m["tl"] == tl
                   and m["axis"] is not None and m["axis"] < floor
                   and plan in by[rk] and by[rk][plan]["settled"]
                   and sold_plan(by, meta, rk) == plan]   # 本番がこの商品を選ぶ行だけ
            if len(sel) < 100:
                continue
            base = [rk for rk in sel if gate_ok(by[rk][plan])]
            print(f"\n  型{tl}／{plan} の下位1/5（axis_sum < {floor:.4f}）"
                  f"  {len(sel):,}レース（うち入稿ゲート通過 {len(base):,}）")
            print(f"    {'売り方':10s}{'n':>7s}{'表示的中':>9s}{'ROI':>7s}{'10万+':>7s}"
                  f"{'払戻中央':>9s}   {'Δ表示的中 95%CI（現行の商品との差・対レース）':<40s}")
            cur = [(by[rk][plan]["inv"], by[rk][plan]["pay"]) for rk in base]
            k = kpi(cur)
            print(f"    {plan+'(現行)':10s}{k['n']:7d}{k['shown']:8.2f}%{k['roi']:6.1f}%"
                  f"{k['big']:6.2f}%{k['med']:9,.0f}   （捨てている）")
            for alt in alts:
                pair = [rk for rk in base
                        if alt in by[rk] and by[rk][alt]["settled"] and gate_ok(by[rk][alt])]
                if len(pair) < 80:
                    continue
                A = [(by[rk][alt]["inv"], by[rk][alt]["pay"]) for rk in pair]
                B = [(by[rk][plan]["inv"], by[rk][plan]["pay"]) for rk in pair]
                ka = kpi(A)
                ds, dlo, dhi, dr, rlo, rhi = paired(A, B)
                print(f"    {alt:10s}{ka['n']:7d}{ka['shown']:8.2f}%{ka['roi']:6.1f}%"
                      f"{ka['big']:6.2f}%{ka['med']:9,.0f}   "
                      f"{ds:+6.2f}pt [{dlo:+.2f},{dhi:+.2f}]  ΔROI {dr:+5.1f}")


if __name__ == "__main__":
    main()


def availability():
    """捨てている下端に、入稿ゲートを通る**代替商品**がどれだけ在るか。"""
    by, meta = load_all()
    print("\n" + "=" * 118)
    print("■ 第10章b 捨てている下端に、入稿ゲート（平均想定払戻2万円超）を通る代替商品はあるか")
    print("=" * 118)
    ALL = ["A_hit", "A_pay", "A_trio", "A_ana", "B_hit", "C_hit",
           "D_hit", "E_hit", "F_hit", "F_pay"]
    for lab, (lo, hi) in (("探索 2025", EXPLORE), ("確認 2026-01〜08", CONFIRM)):
        print(f"\n── {lab}")
        for tl, plan in (("A", "A_hit"), ("D", "D_hit"), ("E", "E_hit"), ("F", "F_hit")):
            floor = GATE.AXIS_GATE_MIN[plan]
            sel = [rk for rk, m in meta.items()
                   if lo <= m["date"] <= hi and m["tl"] == tl
                   and m["axis"] is not None and m["axis"] < floor
                   and plan in by[rk] and by[rk][plan]["settled"]
                   and sold_plan(by, meta, rk) == plan]
            if not sel:
                continue
            cnt = {a: sum(1 for rk in sel
                          if a in by[rk] and by[rk][a]["settled"] and gate_ok(by[rk][a]))
                   for a in ALL}
            txt = "  ".join(f"{a}:{cnt[a]/len(sel)*100:4.0f}%" for a in ALL if a != plan)
            print(f"    型{tl}／{plan} 下端 {len(sel):5,}R → 通過率  {txt}")


if __name__ == "__main__":
    availability()
