#!/usr/bin/env python3
"""第8〜9章 型分類は「狙うべきレース」を切り分けられているか（2026-09-10）。

## 現行設計（`src/type_lab.py`）

`race_shape()` は 2×3 の格子で型を決める:

    firm = axis_sum（3着内率の上位2車の和）>= AXIS_SUM_FIRM(1.44)   … 力関係が堅いか
    s    = arare（ライン人数・先頭の遅れ・脚質・開催日目・番手の得点）… 荒れやすいか
    firm  かつ s<=-1→A  s==0→B  s>=1→C
    混戦  かつ s<=-1→D  s==0→E  s>=1→F

`sell_plans_for()` が型ごとに**仕事の違う商品**を割り当てる:

    A_hit  三連単3点  当てる（絞る）      A_trio 三連複2点 順序を捨てて当てる
    A_ana  三連単5点  軸1を外して穴を取る  B_hit  三連単〜8点 当てる
    C_hit  三連単12点(帯15倍) 当てる       D_hit  三連複4点   当てる
    E_hit  三連単14点(帯30倍) 当てる       F_hit  三連単12点(帯5倍) 当てる
    F_pay  三連単      払戻を取る          F_sign 三連単      看板（高額）

→ **商品の仕事が違うので、表示的中を横並びにして「型Fが弱い」と読んではいけない。**
   本稿は仕事別のKPIで測り、そのうえで**境界そのもの**を動かして影響を見る。

⚠️ paper 行は 2026-08-26 まで。`C_hit` の帯下1点差込（2026-09-08）は入っていない。
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.type_lab import AXIS_SUM_FIRM, sell_plans_for  # noqa: E402
from scripts.exp_type_lab.split_common import (  # noqa: E402
    BOARD, COLS, CONFIRM, EXPLORE, GATE, PAPER, _f, apply_cap, daily_stats,
    gate_ok, load_meta, stats)

HIT_PLANS = {"A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"}
PAY_PLANS = {"F_pay", "F_sign", "A_ana"}


def load_all():
    rp_sd, cup = load_meta()
    by: dict[str, dict[str, dict]] = defaultdict(dict)
    meta: dict[str, dict] = {}
    with PAPER.open() as f:
        for rec in csv.reader(f):
            d = dict(zip(COLS, rec))
            rk = d["race_key"]
            by[rk][d["plan"]] = dict(
                plan=d["plan"], pred_mean=_f(d["pred_mean"]), min_odds=_f(d["min_odds"]),
                inv=_f(d["inv"]) or 0.0, pay=_f(d["payout"]) or 0.0,
                settled=d["settled"] == "1", n_legs=int(d["n_legs"]))
            if rk not in meta:
                meta[rk] = dict(race_key=rk, date=d["date"], venue=d["venue"],
                                race_no=int(d["race_no"]), rtype=d["rtype"],
                                tl=d["tl"], dayidx=int(d["dayidx"]),
                                axis=_f(d["axis"]), arare=_f(d["arare"]),
                                pw_ent=_f(d["pw_ent"]),
                                rp_sd=rp_sd.get(rk), cup_grade=cup.get(rk))
    return by, meta


def type_at(axis, arare, thr) -> str:
    s = int(arare)
    firm = axis >= thr
    if firm:
        return "A" if s <= -1 else ("B" if s == 0 else "C")
    return "D" if s <= -1 else ("E" if s == 0 else "F")


def build(by, meta, thr, window):
    lo, hi = window
    out = []
    for rk, plans in by.items():
        m = meta[rk]
        if not (lo <= m["date"] <= hi) or m["axis"] is None or m["arare"] is None:
            continue
        tl = type_at(m["axis"], m["arare"], thr)
        trio = plans.get("A_trio")
        keys = [p.key for p in sell_plans_for(
            tl, 7, m["rtype"], pw_ent=m["pw_ent"],
            trio_ok=bool(trio) and gate_ok(trio))]
        if len(keys) != 1:
            continue
        r = plans.get(keys[0])
        if r is None:
            continue
        row = dict(m)
        row.update(tl=tl, plan=r["plan"], axis=m["axis"], inv=r["inv"], pay=r["pay"],
                   settled=r["settled"], n_legs=r["n_legs"],
                   ok_gate=gate_ok(r),
                   ok_axis=GATE.passes_axis_gate(r["plan"], m["axis"], 7),
                   exempt=GATE.daily_cap_exempt(m["rtype"], m["cup_grade"]))
        row["prio"] = 1.0 if row["exempt"] else GATE.cap_priority(
            r["plan"], m["axis"], m["rp_sd"])
        out.append(row)
    return out


def mission(sold):
    """仕事別のKPI。`_hit` 系は表示的中、`_pay`/`_sign`/`_ana` は高額。"""
    st = [r for r in sold if r["settled"]]
    h = [r for r in st if r["plan"] in HIT_PLANS]
    p = [r for r in st if r["plan"] in PAY_PLANS]
    def kpi(rows):
        if not rows:
            return None
        inv = np.array([r["inv"] for r in rows]); pay = np.array([r["pay"] for r in rows])
        sh = pay >= inv
        return dict(n=len(rows), shown=sh.mean() * 100,
                    roi=pay.sum() / inv.sum() * 100,
                    big=(pay >= 100_000).mean() * 100,
                    med=float(np.median(pay[sh])) if sh.any() else 0.0)
    return kpi(h), kpi(p), len({r["date"] for r in sold})


def per_type(sold):
    g = defaultdict(list)
    for r in sold:
        if r["settled"]:
            g[(r["tl"], r["plan"])].append(r)
    out = {}
    for k, rows in g.items():
        if len(rows) < 60:
            continue
        inv = np.array([r["inv"] for r in rows]); pay = np.array([r["pay"] for r in rows])
        sh = pay >= inv
        out[k] = dict(n=len(rows), shown=sh.mean() * 100,
                      roi=pay.sum() / inv.sum() * 100,
                      big=(pay >= 100_000).mean() * 100,
                      med=float(np.median(pay[sh])) if sh.any() else 0.0,
                      legs=float(np.mean([r["n_legs"] for r in rows])))
    return out


def main():
    by, meta = load_all()
    WINS = (("探索 2025", EXPLORE), ("確認 2026-01〜08", CONFIRM))

    # ── 第8章 型ごとの「仕事」の達成度 ─────────────────────────────────
    print("=" * 122)
    print("■ 第8章 型ごとの仕事の達成度（現行の境界 1.44・日次上限まで当てたあと）")
    print("=" * 122)
    print(f"  {'型/商品':14s} {'仕事':26s}" + "".join(
        f"{'  n':>7s}{'表示的中':>9s}{'ROI':>7s}{'10万+':>7s}{'払戻中央':>9s}" for _ in WINS))
    JOB = {"A_hit": "絞って当てる(3点)", "A_trio": "順序を捨てて当てる(2点)",
           "A_ana": "軸1を外して穴を取る", "B_hit": "当てる(〜8点)",
           "C_hit": "当てる(12点・帯15倍)", "D_hit": "当てる(三連複4点)",
           "E_hit": "当てる(14点・帯30倍)", "F_hit": "当てる(12点・帯5倍)",
           "F_pay": "払戻を取る", "F_sign": "看板（高額払戻）"}
    tabs = []
    for _lab, w in WINS:
        tabs.append(per_type(apply_cap(build(by, meta, AXIS_SUM_FIRM, w))))
    keys = sorted(set(tabs[0]) | set(tabs[1]))
    for k in keys:
        cells = ""
        for t in tabs:
            v = t.get(k)
            cells += (f"{v['n']:7d}{v['shown']:8.2f}%{v['roi']:6.1f}%{v['big']:6.2f}%"
                      f"{v['med']:9,.0f}" if v else f"{'—':>39s}")
        print(f"  {k[0]}/{k[1]:10s} {JOB.get(k[1],''):26s}{cells}")

    # ── 第9章 境界を動かす ───────────────────────────────────────────
    print("\n" + "=" * 122)
    print("■ 第9章 力関係の境界 AXIS_SUM_FIRM を動かす（型が変わる＝売る商品が変わる）")
    print("=" * 122)
    for lab, w in WINS:
        print(f"\n── {lab}")
        print(f"    {'境界':10s} {'件/日':>6s} {'堅い型%':>7s} | "
              f"{'当てる商品 n':>11s}{'表示的中':>9s}{'ROI':>7s} | "
              f"{'高額商品 n':>10s}{'10万+':>7s}{'払戻中央':>9s} | "
              f"{'日中央%':>7s}{'日SD':>6s}{'半減日%':>7s}")
        for thr in (1.34, 1.39, 1.44, 1.49, 1.54):
            rows = build(by, meta, thr, w)
            sold = apply_cap(rows)
            h, p, nd = mission(sold)
            d = daily_stats(sold)
            firm = np.mean([r["tl"] in "ABC" for r in sold]) * 100
            mark = "（現行）" if thr == AXIS_SUM_FIRM else ""
            print(f"    {thr:.2f}{mark:6s} {len(sold)/max(nd,1):6.2f} {firm:6.1f}% | "
                  f"{h['n']:11d}{h['shown']:8.2f}%{h['roi']:6.1f}% | "
                  f"{p['n']:10d}{p['big']:6.2f}%{p['med']:9,.0f} | "
                  f"{d['med_rate']:6.2f}%{d['sd_rate']:6.2f}{d['p_half']:6.1f}%")

    # ── 境界帯そのもの ───────────────────────────────────────────────
    print("\n" + "=" * 122)
    print("■ 第9章b 境界の近傍（axis_sum 1.34〜1.54）を両側の商品で売ったら")
    print("=" * 122)
    for lab, w in WINS:
        print(f"\n── {lab}")
        lo_, hi_ = w
        band = [rk for rk, m in meta.items()
                if lo_ <= m["date"] <= hi_ and m["axis"] is not None
                and 1.34 <= m["axis"] <= 1.54 and m["arare"] is not None]
        print(f"    帯の中のレース {len(band):,}")
        print(f"    {'arare':10s} {'堅い側の商品':14s}{'n':>6s}{'表示的中':>9s}{'ROI':>7s} | "
              f"{'混戦側の商品':14s}{'n':>6s}{'表示的中':>9s}{'ROI':>7s}")
        for s_lab, pair in (("s<=-1", ("A_hit", "D_hit")), ("s==0", ("B_hit", "E_hit")),
                            ("s>=1", ("C_hit", "F_hit"))):
            sel = [rk for rk in band
                   if (int(meta[rk]["arare"]) <= -1 if s_lab == "s<=-1"
                       else int(meta[rk]["arare"]) == 0 if s_lab == "s==0"
                       else int(meta[rk]["arare"]) >= 1)]
            cells = []
            for pk in pair:
                rows = [by[rk][pk] for rk in sel
                        if pk in by[rk] and by[rk][pk]["settled"] and gate_ok(by[rk][pk])]
                if len(rows) < 30:
                    cells.append(f"{pk:14s}{len(rows):6d}{'—':>9s}{'—':>7s}")
                    continue
                inv = np.array([r["inv"] for r in rows]); pay = np.array([r["pay"] for r in rows])
                cells.append(f"{pk:14s}{len(rows):6d}{(pay>=inv).mean()*100:8.2f}%"
                             f"{pay.sum()/inv.sum()*100:6.1f}%")
            print(f"    {s_lab:10s} " + " | ".join(cells))


if __name__ == "__main__":
    main()
