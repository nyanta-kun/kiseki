#!/usr/bin/env python3
"""型A を3つに分割する（2026-08-31・ユーザー設計）。

    ① 穴狙い     … 軸1が飛ぶと見たレース → 軸1を外した6車から買う
    ② 三連複     … 三連複でも2万円取れるレース → 軸2車＋相手2点
    ③ A_hit     … 残り（現行のまま）

🔴 型は排他で1レース1商品なので、これは**共食いではなく分割**。
   ただし①②は必ず ③ から枠を取るので、**機会費用（A_hit がそのレースで
   出していた成績）を必ず併記する**。
🔴 ①と②の母集団が重なっているかも出す（重なっていれば、分割ではなく
   「同じレースをどちらで売るか」の選択になる）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_split3.py
"""
from __future__ import annotations

import itertools
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np                                     # noqa: E402
import type_a_upset2 as M                              # noqa: E402


def _trio_rank(d):
    cars = sorted(d["o"][1:])
    pr = {frozenset(c): sum(float(d["PROB"][M.CIDX[p]])
                            for p in itertools.permutations(c))
          for c in itertools.combinations(cars, 3)}
    return sorted(pr, key=lambda k: -pr[k])


def _tf_rank(d):
    cars = set(d["o"][1:])
    idx = [i for i, t in enumerate(M.CANON) if set(t) <= cars]
    idx.sort(key=lambda i: -float(d["PROB"][i]))
    return [M.CANON[i] for i in idx]


M.ARMS["穴:飛び三連複3点"] = lambda d: ("trio", _trio_rank(d)[:3])
M.ARMS["穴:飛び三連単5点"] = lambda d: ("tf", _tf_rank(d)[:5])
M.ARMS["三連複2点"] = lambda d: ("trio", [frozenset((d["o"][0], d["o"][1], d["o"][k]))
                                       for k in (2, 3)])

HDR = ("    {:<26}{:>7}{:>9}{:>10}{:>9}{:>10}{:>10}{:>18}"
       .format("構成", "件/日", "表示的中", "払戻中央", "10万+本", "10万+/日", "30万+/日", "ROI(CI95)"))


def kpi(recs, nd):
    if not recs:
        return None
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    h = [r for r in recs if r["pay"] > 0]
    sh = [r for r in h if r["pay"] > r["inv"]]
    ps = sorted(r["pay"] for r in h)
    big = [x for x in ps if x >= 100_000]
    lo, hi = M.boot_roi(recs)
    return dict(perday=len(recs)/nd, shown=len(sh)/len(recs)*100,
                med=median(ps) if ps else 0, big_n=len(big), big=len(big)/nd,
                huge=sum(1 for x in ps if x >= 300_000)/nd,
                roi=pay/inv*100 if inv else 0, lo=lo, hi=hi,
                inv=inv, pay=pay, n=len(recs), shown_n=len(sh))


def row(name, s):
    if not s:
        return f"    {name:<26}  (該当なし)"
    return (f"    {name:<26}{s['perday']:>7.2f}{s['shown']:>8.2f}%{s['med']:>10,.0f}"
            f"{s['big_n']:>9}{s['big']:>10.3f}{s['huge']:>10.3f}"
            f"   {s['roi']:>5.1f}[{s['lo']:.0f},{s['hi']:.0f}]")


def build(rs, ana_sel, ana_arm):
    """①穴狙い → ②三連複2点 → ③A_hit の順に割り当てる。"""
    out, opp = [], []
    for d in rs:
        if ana_sel(d):
            r = M.play(d, ana_arm)
            if r:
                out.append(dict(r, bucket="穴"))
                a = M.play(d, "A_hit 現行3点")     # 機会費用
                if a:
                    opp.append(a)
                continue
        r = M.play(d, "三連複2点")
        if r:
            out.append(dict(r, bucket="三連複"))
            continue
        r = M.play(d, "A_hit 現行3点")
        if r:
            out.append(dict(r, bucket="A_hit"))
    return out, opp


def main() -> int:
    data = M.load()
    ex = [d for d in data if M.WINDOWS["探索 2025"][0] <= d["date"] <= M.WINDOWS["探索 2025"][1]]
    pe = {q: sorted(d["pw_ent"] for d in ex)[int(len(ex)*(1-q))] for q in (.33, .20, .10)}
    # 型ラボ全体（本番2ゲート込み・`lineup_signboard.py` と同じ数字）
    LU = {"探索 2025": dict(perday=59.26, shown=18.30, roi=72.9, big=0.556),
          "確認 2026": dict(perday=47.39, shown=19.51, roi=77.7, big=0.458)}

    for win, (lo, hi) in M.WINDOWS.items():
        rs = [d for d in data if lo <= d["date"] <= hi]
        nd = len({d["date"] for d in rs})
        print(f"\n{'='*122}\n=== {win}  型A {len(rs):,}R / {nd}日 ===")

        # ①と②の重なり
        for q in (.33, .20, .10):
            a = {d["key"] for d in rs if d["pw_ent"] > pe[q]}
            b = {d["key"] for d in rs if M.play(d, "三連複2点")}
            print(f"  重なり pw_ent上位{int(q*100):>2}% ({len(a):,}R) ∩ 三連複2点が通る ({len(b):,}R) "
                  f"= {len(a & b):,}R（穴狙い側の {len(a & b)/max(len(a),1):.0%} / 三連複側の {len(a & b)/max(len(b),1):.0%}）")

        print(f"\n{HDR}")
        base, _ = build(rs, lambda d: False, "穴:飛び三連複3点")
        base = [r for r in base if r["bucket"] == "A_hit"]
        cur = [r for r in (M.play(d, "A_hit 現行3点") for d in rs) if r]
        print(row("現行（A_hit のみ）", kpi(cur, nd)))
        b_only, _ = build(rs, lambda d: False, "穴:飛び三連複3点")
        print(row("②のみ（A_hit＋三連複2点）", kpi(b_only, nd)))
        for q in (.33, .20, .10):
            for arm in ("穴:飛び三連複3点", "穴:飛び三連単5点"):
                recs, opp = build(rs, (lambda t: lambda d: d["pw_ent"] > t)(pe[q]), arm)
                s = kpi(recs, nd)
                print(row(f"3分割 上位{int(q*100)}%×{arm.split(':')[1]}", s))
        # 機会費用: 穴狙いに回したレースで A_hit は何を出していたか
        print(f"\n  ── 機会費用（穴狙いに回すレースで、A_hit なら何が出ていたか）──")
        print(f"    {'選別':<14}{'件/日':>7}{'A_hit 表示的中':>15}{'A_hit ROI':>11}{'A_hit 10万+/日':>15}"
              f"{'穴 表示的中':>12}{'穴 ROI':>9}{'穴 10万+/日':>12}")
        for q in (.33, .20, .10):
            sel = [d for d in rs if d["pw_ent"] > pe[q]]
            a = kpi([r for r in (M.play(d, "A_hit 現行3点") for d in sel) if r], nd)
            g = kpi([r for r in (M.play(d, "穴:飛び三連複3点") for d in sel) if r], nd)
            if a and g:
                print(f"    上位{int(q*100):>2}%{'':<8}{g['perday']:>7.2f}{a['shown']:>14.2f}%"
                      f"{a['roi']:>11.1f}{a['big']:>15.3f}{g['shown']:>11.2f}%{g['roi']:>9.1f}{g['big']:>12.3f}")

        # ラインナップ全体
        print(f"\n  ── ラインナップ全体への影響（型A 以外は不変とみなす）──")
        tot = LU[win]
        oth_n = tot["perday"] - kpi(cur, nd)["perday"]
        oth_shown = (tot["shown"]*tot["perday"] - kpi(cur, nd)["shown"]*kpi(cur, nd)["perday"]) / oth_n
        oth_big = tot["big"] - kpi(cur, nd)["big"]
        print(f"    {'構成':<26}{'全体件/日':>10}{'全体表示的中':>13}{'全体10万+/日':>14}")
        print(f"    {'現行':<26}{tot['perday']:>10.2f}{tot['shown']:>12.2f}%{tot['big']:>14.3f}")
        for lab, recs in [("②のみ（A_hit＋三連複2点）", b_only)] + [
                (f"3分割 上位{int(q*100)}%×{arm.split(':')[1]}",
                 build(rs, (lambda t: lambda d: d["pw_ent"] > t)(pe[q]), arm)[0])
                for q in (.33, .20, .10) for arm in ("穴:飛び三連複3点", "穴:飛び三連単5点")]:
            s = kpi(recs, nd)
            n = oth_n + s["perday"]
            sh = (oth_shown*oth_n + s["shown"]*s["perday"]) / n
            bg = oth_big + s["big"]
            print(f"    {lab:<26}{n:>10.2f}{sh:>12.2f}%{bg:>14.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
