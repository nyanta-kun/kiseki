#!/usr/bin/env python3
"""`order_depth.py` の pkl を突き合わせる。

    python order_depth_cmp.py /tmp/order_depth.pkl [--plan E_hit] [--axis-gate]

🔴 判定は **⑥ ctl_d2（無作為対照）に勝つか**。ROI 単独では決めない（この層は
   ±2.5pt を詰めるのに15年かかる）。確認窓(2026)が本番相当。
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C                                              # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("pkl")
ap.add_argument("--plan", default=None, help="プランで絞る（カンマ区切り）")
ap.add_argument("--axis-gate", action="store_true", help="軸信頼ゲート通過だけ")
ap.add_argument("--nboot", type=int, default=4000)
a = ap.parse_args()

D = pickle.load(Path(a.pkl).open("rb"))
ROWS, NDAYS = D["rows"], D["ndays"]
PLANS = set(a.plan.split(",")) if a.plan else None
RNG = np.random.default_rng(20260921)


def sel(rows, win):
    out = [r for r in rows if r["win"] == win and r["gate"]]
    if PLANS:
        out = [r for r in out if r["key"] in PLANS]
    if a.axis_gate:
        out = [r for r in out if r["axis_ok"]]
    return out


def stats(rs, nd):
    if not rs:
        return dict(n=0)
    s = C.summarize(rs, nd)
    s["sethit"] = sum(1 for r in rs if r["sethit"]) / len(rs) * 100
    s["exact"] = sum(1 for r in rs if r["exact"]) / len(rs) * 100
    s["nsets"] = sum(r["nsets"] for r in rs) / len(rs)
    # 🔴 診断: 先頭の目の集合が決着した回だけを見る（「1点で足りるか」の答え）
    fh = [r for r in rs if r.get("first_hit")]
    s["first_rate"] = len(fh) / len(rs) * 100
    s["first_exact"] = (sum(1 for r in fh if r["exact"]) / len(fh) * 100) if fh else 0.0
    s["first_shown"] = (sum(1 for r in fh if r["pay"] > r["inv"]) / len(fh) * 100) if fh else 0.0
    s["n_first"] = sum(r.get("n_first", 0) for r in rs) / len(rs)
    s["sigma"] = sum(r.get("sigma", 0.0) for r in rs) / len(rs)
    won = [r for r in rs if r["pay"] > 0 and r.get("win_stake")]
    s["win_stake"] = median([r["win_stake"] for r in won]) if won else 0.0
    s["big"] = s["big_per_day"]
    return s


def shown_vec(rs):
    return np.array([1.0 if (r["pay"] > r["inv"]) else 0.0 for r in rs])


def boot_roi(base, arm):
    """ΔROI のレース単位ブートストラップ。**ROI 単独で採否は決めないが、
    代償が「判別できる大きさか」は見る必要がある。**"""
    ib = {r["i"]: r for r in base}
    ia = {r["i"]: r for r in arm}
    keys = sorted(set(ib) & set(ia))
    if not keys:
        return None
    bp = np.array([ib[k]["pay"] for k in keys])
    bi = np.array([ib[k]["inv"] for k in keys])
    ap = np.array([ia[k]["pay"] for k in keys])
    ai = np.array([ia[k]["inv"] for k in keys])
    n = len(keys)
    d0 = (ap.sum() / ai.sum() - bp.sum() / bi.sum()) * 100
    bs = np.empty(a.nboot)
    for t in range(a.nboot):
        j = RNG.integers(0, n, n)
        bs[t] = (ap[j].sum() / ai[j].sum() - bp[j].sum() / bi[j].sum()) * 100
    return d0, float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def boot_delta(base, arm):
    """レース単位の対応ありブートストラップ（同じレース集合で比べる）。"""
    ib = {r["i"]: r for r in base}
    ia = {r["i"]: r for r in arm}
    keys = sorted(set(ib) & set(ia))
    if not keys:
        return None
    b = np.array([1.0 if ib[k]["pay"] > ib[k]["inv"] else 0.0 for k in keys])
    x = np.array([1.0 if ia[k]["pay"] > ia[k]["inv"] else 0.0 for k in keys])
    d = (x - b) * 100
    n = len(keys)
    bs = np.array([d[RNG.integers(0, n, n)].mean() for _ in range(a.nboot)])
    return d.mean(), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5)), n


NAME = {"base": "⓪ 現行", "add_odds2": "2点足す(安い順)",
        "add_rand_odds2": "対照(無作為2点)",
        "add_full": "先頭集合を全部足す", "swap_tail1": "末尾1点と入れ替え",
        "swap_tail2": "末尾2点と入れ替え", "ctl_tail1": "対照(末尾1点↔無作為)",
        "add_odds": "⑨ 1点足す(オッズ最安)", "d2": "① 深さ d=2", "d3": "② 深さ d=3",
        "top1_full": "③ 最上位集合を全順列", "swap_odds": "④ 集合内を予測オッズ順",
        "d2_odds": "⑤ d=2 + オッズ順", "ctl_d2": "⑥ 無作為対照(d=2)"}

for win in ("explore", "confirm"):
    lab = "探索 2024-07〜2025-12" if win == "explore" else "確認 2026-01〜08"
    print(f"\n{'='*118}\n{lab}   プラン={a.plan or '全部'}"
          f"{'  軸ゲート通過のみ' if a.axis_gate else ''}\n{'='*118}")
    nd = NDAYS[win]
    base = sel(ROWS["base"], win)
    print("  {:24s} {:>6s} {:>5s} {:>6s} {:>7s} {:>7s} {:>8s} {:>9s} {:>8s} {:>7s}".format(
        "腕", "件/日", "点数", "集合数", "集合的中", "的中%", "表示的中%", "払戻中央",
        "10万+/日", "ROI%"))
    for k in ROWS:
        rs = sel(ROWS[k], win)
        s = stats(rs, nd)
        if not s.get("n"):
            print(f"  {NAME.get(k,k):24s}  (該当なし)")
            continue
        print("  {:24s} {:6.2f} {:5.2f} {:6.2f} {:7.2f} {:7.2f} {:8.2f} {:9,.0f} {:8.3f} {:7.1f}"
              .format(NAME.get(k, k), s["perday"], s["k"], s["nsets"], s["sethit"],
                      s["hit"], s["shown"], s["med_pay"], s["big"], s["roi"]))
    print("\n  診断 — 先頭の目の集合が決着した回だけ（『1点で足りるか』）")
    print("  {:24s} {:>8s} {:>9s} {:>10s} {:>8s} {:>7s} {:>10s}".format(
        "腕", "先頭集合", "うち的中", "うち表示的中", "先頭点数", "Σ(1/O)", "当たり賭け金"))
    for k in ROWS:
        rs = sel(ROWS[k], win)
        s = stats(rs, nd)
        if not s.get("n"):
            continue
        print("  {:24s} {:7.2f}% {:8.2f}% {:9.2f}% {:8.2f} {:7.4f} {:9,.0f}円".format(
            NAME.get(k, k), s["first_rate"], s["first_exact"], s["first_shown"],
            s["n_first"], s["sigma"], s["win_stake"]))

    print("\n  Δ表示的中（対 ⓪現行・レース単位ブートストラップ95%CI）")
    for k in ROWS:
        if k == "base":
            continue
        r = boot_delta(base, sel(ROWS[k], win))
        if not r:
            continue
        m, lo, hi, n = r
        sig = "★" if (lo > 0 or hi < 0) else " "
        rr = boot_roi(base, sel(ROWS[k], win))
        rs = ""
        if rr:
            rm, rlo, rhi = rr
            rsig = "★" if (rlo > 0 or rhi < 0) else " "
            rs = f"   ΔROI {rm:+5.2f}pt CI[{rlo:+5.2f},{rhi:+5.2f}]{rsig}"
        print(f"   {sig} {NAME.get(k,k):24s} {m:+6.2f}pt  CI[{lo:+6.2f},{hi:+6.2f}]  n={n:,}{rs}")
