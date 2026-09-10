#!/usr/bin/env python3
"""第3〜4章 日次KPI の分解と「件数（＝日次上限）」というダイヤル（2026-09-10）。

第3章: 日次の的中率のばらつきを「二項ノイズ」と「その日の商品構成」に分ける。
第4章: `DAILY_CAP_RACE_FRACTION` を掃引し、日単位KPI がどう動くかを見る。
       件数を変える腕なので**無作為対照20本**を同時に出す。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.exp_type_lab.split_common import (  # noqa: E402
    DHDR, HDR, apply_cap, daily_frame, daily_stats, dline, line, load_races,
    stats, window)


def decompose(sold):
    """日次の的中率 SD を「二項」と「構成（プラン構成）」へ分ける。"""
    st = [r for r in sold if r["settled"]]
    y = np.array([1.0 if r["pay"] >= r["inv"] else 0.0 for r in st])
    plan = np.array([r["plan"] for r in st])
    day = np.array([r["date"] for r in st])
    prate = {p: y[plan == p].mean() for p in np.unique(plan)}
    idx = defaultdict(list)
    for i, d in enumerate(day):
        idx[d].append(i)
    days = [d for d in idx if len(idx[d]) >= 5]
    obs, exp, binom = [], [], []
    for d in days:
        j = np.array(idx[d])
        obs.append(y[j].mean())
        p = np.array([prate[x] for x in plan[j]])
        exp.append(p.mean())
        binom.append((p * (1 - p)).sum() / len(j) ** 2)
    obs, exp, binom = np.array(obs), np.array(exp), np.array(binom)
    return dict(sd_obs=obs.std() * 100, sd_comp=exp.std() * 100,
                sd_binom=np.sqrt(binom.mean()) * 100,
                sd_pred=np.sqrt(exp.var() + binom.mean()) * 100,
                n_days=len(days))


def roi_null(sold, seed=0, iters=1000):
    """日次ROI の散らばりが「同じ件数で無作為に商品を引いた」場合と違うか。"""
    st = [r for r in sold if r["settled"]]
    inv = np.array([r["inv"] for r in st])
    pay = np.array([r["pay"] for r in st])
    F = daily_frame(sold)
    m = F["n"] >= 5
    ns = F["n"][m]
    roi = F["pay"][m] / np.maximum(F["inv"][m], 1) * 100
    obs_sd = roi.std()
    obs_p100 = (roi >= 100).mean() * 100
    rng = np.random.default_rng(seed)
    sds, p100 = [], []
    for _ in range(iters):
        v = []
        for n in ns:
            j = rng.integers(0, len(inv), n)
            v.append(pay[j].sum() / max(inv[j].sum(), 1) * 100)
        v = np.array(v)
        sds.append(v.std()); p100.append((v >= 100).mean() * 100)
    return (obs_sd, float(np.median(sds)), float(np.percentile(sds, 2.5)),
            float(np.percentile(sds, 97.5)), obs_p100, float(np.median(p100)))


def control(races, k_target, seed, keep_exempt=True):
    """同じ件数を無作為に選ぶ対照（上限の順位付けだけを無作為に差し替える）。"""
    rng = np.random.default_rng(seed)
    return apply_cap(races, prio=lambda r: rng.random(), keep_exempt=keep_exempt)


def main():
    R = load_races()
    for wn, lab in (("explore", "探索 2025"), ("confirm", "確認 2026-01〜08")):
        rs = window(R, wn)
        base = apply_cap(rs)
        print(f"\n{'='*118}\n■ {lab}\n{'='*118}")
        d = decompose(base)
        print(f"  第3章 日次の的中率 SD の分解（{d['n_days']}日）")
        print(f"    実測 {d['sd_obs']:.2f}pt = 二項 {d['sd_binom']:.2f}pt "
              f"⊕ 構成 {d['sd_comp']:.2f}pt → 予測 {d['sd_pred']:.2f}pt")
        o, med, lo, hi, op, mp = roi_null(base)
        print(f"  第3章b 日次ROI の SD: 実測 {o:.1f}pt ↔ "
              f"無作為同数 {med:.1f}pt [{lo:.1f},{hi:.1f}]  "
              f"／ ROI100%超の日 実測 {op:.1f}% ↔ 無作為 {mp:.1f}%")
        print(f"\n  第4章 日次上限の掃引\n  {HDR}")
        arms = [("上限なし", 0.0), ("0.9", 0.9), ("0.7", 0.7), ("0.5（現行）", 0.5),
                ("0.35", 0.35), ("0.25", 0.25)]
        keep = {}
        for name, f in arms:
            s = apply_cap(rs, fraction=f)
            keep[name] = s
            print("  " + line(name, stats(s)))
        print(f"\n  {DHDR}")
        for name, _f in arms:
            print("  " + dline(name, daily_stats(keep[name])))
        # 無作為対照（現行 0.5 の順位付けを無作為へ）
        print("\n  無作為対照20本（上限0.5・残す順だけ無作為）")
        cs, cr, cd = [], [], []
        for sd in range(20):
            s = control(rs, None, sd)
            st = stats(s); ds = daily_stats(s)
            cs.append(st["shown"]); cr.append(st["roi"]); cd.append(ds["sd_rate"])
        b = stats(base); bd = daily_stats(base)
        print(f"    表示的中 現行 {b['shown']:.2f}% ↔ 対照中央 {np.median(cs):.2f}% "
              f"（勝ち {sum(b['shown']>c for c in cs)}/20）")
        print(f"    ROI      現行 {b['roi']:.1f}%  ↔ 対照中央 {np.median(cr):.1f}%  "
              f"（勝ち {sum(b['roi']>c for c in cr)}/20）")
        print(f"    日次SD   現行 {bd['sd_rate']:.2f}pt ↔ 対照中央 {np.median(cd):.2f}pt "
              f"（小さい方が勝ち {sum(bd['sd_rate']<c for c in cd)}/20）")


if __name__ == "__main__":
    main()
