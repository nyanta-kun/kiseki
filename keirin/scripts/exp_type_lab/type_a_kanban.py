#!/usr/bin/env python3
"""提案A の深掘り: 「軸が飛ぶ側」を**看板商品**として成立させられるか（2026-08-31）。

🔴 判断軸を ROI から **10万円超の本数**へ移して測る。ただし
   ①その本数が何本の的中で作られているか（数本の幸運でないか）
   ②四半期ごとに再現するか
   ③ラインナップ全体の ROI を何pt下げるか
   を必ず併記する。無作為対照も置く（件数を減らす検証の鉄則）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_kanban.py
"""
from __future__ import annotations

import itertools
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np                                    # noqa: E402
import type_a_upset2 as M                             # noqa: E402
from src.database import get_connection               # noqa: E402
from src.marquee import is_fill_target                # noqa: E402
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS   # noqa: E402
from src.type_lab import SELL_PLANS                   # noqa: E402
import importlib.util                                 # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", Path(__file__).resolve().parents[3] / "backend/src/services/keirin_type_lab_gate.py")
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)   # type: ignore


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


for k in (3, 5, 8):
    M.ARMS[f"飛び三連複{k}点"] = (lambda k: lambda d: ("trio", _trio_rank(d)[:k]))(k)
for k in (5, 10):
    M.ARMS[f"飛び三連単{k}点"] = (lambda k: lambda d: ("tf", _tf_rank(d)[:k]))(k)

ARMS = ["飛び三連複3点", "飛び三連複5点", "飛び三連複8点", "飛び三連単5点", "飛び三連単10点"]


def kpi(recs, nd):
    if not recs:
        return None
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    shown = [r for r in hits if r["pay"] > r["inv"]]
    ps = sorted(r["pay"] for r in hits)
    big = [x for x in ps if x >= 100_000]
    lo, hi = M.boot_roi(recs)
    return dict(n=len(recs), perday=len(recs)/nd, shown=len(shown)/len(recs)*100,
                med=median(ps) if ps else 0, roi=pay/inv*100 if inv else 0,
                lo=lo, hi=hi, big_n=len(big), big=len(big)/nd,
                huge=sum(1 for x in ps if x >= 300_000)/nd,
                mx=max(ps) if ps else 0, invday=inv/nd)


HDR = ("    {:<18}{:>6}{:>9}{:>10}{:>9}{:>8}{:>10}{:>11}{:>10}"
       .format("腕", "件/日", "表示的中", "払戻中央", "投資/日", "10万+本", "10万+/日", "30万+/日", "最大払戻"))


def row(name, s):
    if not s:
        return f"    {name:<18}  (該当なし)"
    return (f"    {name:<18}{s['perday']:>6.2f}{s['shown']:>8.2f}%{s['med']:>10,.0f}"
            f"{s['invday']:>9,.0f}{s['big_n']:>8}{s['big']:>10.3f}{s['huge']:>11.3f}"
            f"{s['mx']:>10,.0f}   ROI {s['roi']:.1f}[{s['lo']:.0f},{s['hi']:.0f}]")


def lineup_total():
    """型ラボが実際に売っている全体（本番の2ゲート込み）の件/日と ROI。"""
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT race_date, race_type, axis_sum, plan_key, n_entries, legs, "
            "       pred_mean_payout, payout FROM type_lab_picks "
            "WHERE mode IN ('paper','paper9') AND settled_at IS NOT NULL AND budget > 0")]
    out = defaultdict(lambda: [0, 0.0, 0.0, set()])
    for d in rows:
        if d["plan_key"] not in SELL_PLANS:
            continue
        legs = d["legs"] if isinstance(d["legs"], list) else json.loads(d["legs"] or "[]")
        if not legs:
            continue
        if not (is_fill_target(d.get("race_type"), None) or G.passes_axis_gate(
                d["plan_key"], float(d["axis_sum"]) if d["axis_sum"] is not None else None,
                int(d["n_entries"]) if d["n_entries"] else None)):
            continue
        mp = d["pred_mean_payout"]
        if mp is not None and float(mp) <= MIN_MEAN_PAYOUT:
            continue
        po = [float(l.get("pred_odds") or 0) for l in legs]
        po = [x for x in po if x > 0]
        if po and min(po) < MIN_POINT_ODDS:
            continue
        y = "探索 2025" if str(d["race_date"]) <= "2025-12-31" else "確認 2026"
        a = out[y]
        a[0] += 1; a[1] += sum(int(l["stake"]) for l in legs)
        a[2] += int(d["payout"] or 0); a[3].add(str(d["race_date"]))
    return {k: dict(n=v[0], perday=v[0]/len(v[3]), roi=v[2]/v[1]*100) for k, v in out.items()}


def main() -> int:
    data = M.load()
    ex = [d for d in data if M.WINDOWS["探索 2025"][0] <= d["date"] <= M.WINDOWS["探索 2025"][1]]
    pe = {q: sorted(d["pw_ent"] for d in ex)[int(len(ex) * (1 - q))] for q in (.33, .20, .10, .05)}
    print("選別の境界は探索窓の分位（確認窓へそのまま当てる）: "
          + " / ".join(f"上位{int(q*100)}% > {v:.4f}" for q, v in pe.items()))
    LU = lineup_total()
    print("型ラボ全体（本番2ゲート込み・売っている分）: "
          + " / ".join(f"{k} {v['perday']:.1f}件日・ROI {v['roi']:.1f}%" for k, v in LU.items()))

    for win, (lo, hi) in M.WINDOWS.items():
        rs = [d for d in data if lo <= d["date"] <= hi]
        nd = len({d["date"] for d in rs})
        print(f"\n{'='*128}\n=== {win}  型A {len(rs):,}R / {nd}日 ===")
        sels = [("型A 全部", rs)] + [(f"pw_ent 上位{int(q*100)}%",
                                     [d for d in rs if d["pw_ent"] > pe[q]]) for q in (.33, .20, .10, .05)]
        for sname, sub in sels:
            b = sum(1 for d in sub if d["o"][0] not in d["f"])
            print(f"\n  ── {sname}  {len(sub):,}R  軸崩壊 {b/len(sub):.1%} ──")
            print(HDR)
            for a in ARMS:
                print(row(a, kpi([r for r in (M.play(d, a) for d in sub) if r], nd)))
        # 安定性: 四半期ごとの 10万+ 本数（代表2案）
        print(f"\n  ── 四半期ごとの 10万+ 本数（型A 全部）──")
        for a in ("飛び三連複5点", "飛び三連単5点"):
            q = defaultdict(lambda: [0, 0])
            for d in rs:
                r = M.play(d, a)
                if not r:
                    continue
                k = f"{d['date'][:4]}Q{(int(d['date'][5:7])-1)//3+1}"
                q[k][0] += 1
                q[k][1] += int(r["pay"] >= 100_000)
            print(f"    {a:<12} " + "  ".join(f"{k} {v[1]}本/{v[0]}件" for k, v in sorted(q.items())))
        # 無作為対照（上位10% と同数）
        sel = [d for d in rs if d["pw_ent"] > pe[.10]]
        print(f"\n  ── 無作為に同数（{len(sel):,}R）を取った対照 20本（10万+/日 と ROI）──")
        for a in ("飛び三連複5点", "飛び三連単5点"):
            bb, rr = [], []
            for sd in range(20):
                p = random.Random(sd).sample(rs, len(sel))
                s = kpi([r for r in (M.play(d, a) for d in p) if r], nd)
                if s: bb.append(s["big"]); rr.append(s["roi"])
            bb.sort(); rr.sort()
            real = kpi([r for r in (M.play(d, a) for d in sel) if r], nd)
            print(f"    {a:<12} 対照 10万+/日 中央 {bb[10]:.3f}[{bb[0]:.3f},{bb[-1]:.3f}] "
                  f"ROI 中央 {rr[10]:.1f}   ↔ 選別 10万+/日 {real['big']:.3f} ROI {real['roi']:.1f}")
        # ラインナップ全体への影響
        print(f"\n  ── ラインナップ全体への影響（型A の一部を振り替えたとき）──")
        tot = LU[win]
        for sname, sub in sels:
            for a in ("飛び三連複5点",):
                s = kpi([r for r in (M.play(d, a) for d in sub) if r], nd)
                cur = kpi([r for r in (M.play(d, "A_hit 現行3点") for d in sub) if r], nd)
                if not s or not cur:
                    continue
                share = s["perday"] / tot["perday"]
                d_roi = share * (s["roi"] - cur["roi"])
                print(f"    {sname:<16} 置換 {s['perday']:.2f}件/日（全体の {share:.1%}）  "
                      f"全体ROI {tot['roi']:.1f}% → {tot['roi']+d_roi:.1f}%（{d_roi:+.2f}pt）  "
                      f"10万+ {cur['big']:.3f} → {s['big']:.3f}件/日")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
