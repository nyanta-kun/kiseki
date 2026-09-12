#!/usr/bin/env python3
"""腕の比較（件/日・表示的中・払戻中央・10万+/日・ガミ・ROI）＋ paired bootstrap。

使い方:
  03_cmp.py grid  [型]          プール×計画払戻×点数上限の格子
  03_cmp.py cover                プールの的中上限（決着の目が入っているか）
  03_cmp.py duel  基準 対抗 ...  同一レース対比較（paired bootstrap 95%CI）
  03_cmp.py ov                   重ね買い（下帯8割＋上帯2割）
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from statistics import median

import numpy as np

ROWS = pickle.loads(open("/tmp/08_mst_rows.pkl", "rb").read())
WINS = (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm"))
NDAYS = {w: len({r["date"] for r in ROWS if r["win"] == w})
         for w in ("explore", "confirm")}


def kpi(recs, nd):
    n = len(recs)
    if not n:
        return None
    inv = sum(r["inv"] for r in recs)
    pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    return dict(
        n=n, perday=n / nd, k=sum(r["k"] for r in recs) / n,
        hit=len(hits) / n * 100,
        gami=len(gami) / max(len(hits), 1) * 100,
        shown=(len(hits) - len(gami)) / n * 100,
        med=median(pays) if pays else 0.0,
        medmean=median(sorted(r["mean"] for r in recs)),
        two=sum(1 for r in hits if r["pay"] >= 2 * r["inv"]) / nd,
        big=sum(1 for p in pays if p >= 100_000) / nd,
        big3=sum(1 for p in pays if p >= 300_000) / nd,
        bigr=sum(1 for p in pays if p >= 100_000) / n * 100,
        bigr3=sum(1 for p in pays if p >= 300_000) / n * 100,
        roi=pay / inv * 100 if inv else 0.0)


HEAD = ("  {:24s} {:>6s} {:>6s} {:>7s} {:>6s} {:>8s} {:>9s} {:>10s} {:>7s} "
        "{:>8s} {:>8s} {:>8s} {:>8s} {:>6s}".format(
            "腕", "件/日", "点数", "的中%", "ガミ%", "表示的中", "払戻中央",
            "計画払戻中央", "2倍+/日", "10万+/日", "30万+/日",
            "10万+率/件", "30万+率/件", "ROI%"))


def line(name, s):
    if not s:
        return f"  {name:24s}  (該当なし)"
    return (f"  {name:24s} {s['perday']:6.2f} {s['k']:6.2f} {s['hit']:7.2f} "
            f"{s['gami']:6.2f} {s['shown']:8.2f} {s['med']:9,.0f} "
            f"{s['medmean']:10,.0f} {s['two']:7.2f} {s['big']:8.3f} "
            f"{s['big3']:8.3f} {s['bigr']:8.2f} {s['bigr3']:8.2f} {s['roi']:6.1f}")


def sel(win, tfilter=None):
    out = [r for r in ROWS if r["win"] == win]
    if tfilter:
        out = [r for r in out if r["type"] in tfilter]
    return out


def grid(tfilter=None):
    pools = ("all", "bust", "Mall", "N", "N_hon", "N_pw2", "M")
    for lbl, win in WINS:
        rs = sel(win, tfilter)
        nd = NDAYS[win]
        print(f"\n{'='*140}\n=== {lbl}  n={len(rs):,}R  日数={nd}"
              f"  型={tfilter or 'ABCDEF'} ===")
        for t in (20_000, 50_000, 100_000, 150_000, 400_000):
            print(f"\n-- 計画払戻 T={t:,}円 --")
            print(HEAD)
            for p in pools:
                for cap in (0, 12, 8, 4):
                    key = f"{p}@{t}k{cap}"
                    recs = [r["arms"][key] for r in rs if key in r["arms"]]
                    s = kpi(recs, nd)
                    if s and s["n"] >= 30:
                        tag = f"{p} 上限{cap or '無'}"
                        print(line(tag, s))


def cover():
    for lbl, win in WINS:
        rs = sel(win)
        print(f"\n=== {lbl}  n={len(rs):,}R — プールの的中上限 ===")
        print(f"  {'プール':8s} {'平均点数':>8s} {'決着が入る%':>10s} "
              f"{'その払戻中央(倍)':>16s} {'100倍+%':>8s}")
        for p in ("all", "bust", "Mall", "N", "N_hon", "N_pw2", "M"):
            ok = [r for r in rs if p in r["inpool"]]
            if not ok:
                continue
            inn = [r for r in ok if r["inpool"][p]]
            pays = sorted(r["pay_tf"] for r in inn)
            print(f"  {p:8s} {sum(r['poolsz'][p] for r in ok)/len(ok):8.1f} "
                  f"{len(inn)/len(ok)*100:9.2f}% "
                  f"{median(pays) if pays else 0:15,.0f} "
                  f"{sum(1 for x in pays if x>=100)/max(len(pays),1)*100:7.2f}%")


def _vec(recs):
    """(inv, pay) の配列。"""
    return (np.array([r["inv"] for r in recs], float),
            np.array([r["pay"] for r in recs], float))


def _mk(recs):
    """paired bootstrap 用の指標素材（レースごとのスカラー列）。"""
    inv, pay = _vec(recs)
    hit = pay > 0
    shown = (hit & (pay >= inv)).astype(float)
    big = (pay >= 100_000).astype(float)
    big3 = (pay >= 300_000).astype(float)
    two = (pay >= 2 * inv).astype(float)
    return dict(inv=inv, pay=pay, shown=shown, big=big, big3=big3, two=two)


METRICS = (("表示的中", "shown"), ("10万+率", "big"), ("30万+率", "big3"),
           ("2倍+率", "two"))


def boot2(a, b, n=4000, seed=7):
    """レース単位 paired bootstrap。a/b は `_mk` の出力。戻り: {指標: (Δ, lo, hi)}。"""
    rng = np.random.default_rng(seed)
    m = len(a["inv"])
    idx = rng.integers(0, m, size=(n, m))
    out = {}
    for lbl, key in METRICS:
        da, db = a[key], b[key]
        sa = da[idx].mean(1) * 100
        sb = db[idx].mean(1) * 100
        d = sb - sa
        out[lbl] = (db.mean() * 100 - da.mean() * 100,
                    float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)))
    ra = a["pay"][idx].sum(1) / a["inv"][idx].sum(1) * 100
    rb = b["pay"][idx].sum(1) / b["inv"][idx].sum(1) * 100
    d = rb - ra
    out["ROI"] = (b["pay"].sum() / b["inv"].sum() * 100
                  - a["pay"].sum() / a["inv"].sum() * 100,
                  float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)))
    return out


def duel(base_key, *others, tfilter=None):
    for lbl, win in WINS:
        rs = sel(win, tfilter)
        nd = NDAYS[win]
        print(f"\n=== {lbl}  基準 {base_key}  型={tfilter or 'ABCDEF'} ===")
        print(HEAD)
        b = [r["arms"][base_key] for r in rs if base_key in r["arms"]]
        print(line(base_key, kpi(b, nd)))
        for o in others:
            pairs = [(r["arms"][base_key], r["arms"][o]) for r in rs
                     if base_key in r["arms"] and o in r["arms"]]
            if len(pairs) < 30:
                print(f"  {o:24s}  (共通 n={len(pairs)} 不足)")
                continue
            recs = [p[1] for p in pairs]
            print(line(o, kpi(recs, nd)))
            res = boot2(_mk([p[0] for p in pairs]), _mk(recs))
            print("      " + "  ".join(
                f"Δ{k} {v[0]:+.2f} [{v[1]:+.2f},{v[2]:+.2f}]"
                for k, v in res.items()) + f"  (共通n={len(pairs)})")


def ov():
    for lbl, win in WINS:
        rs = sel(win)
        nd = NDAYS[win]
        base = [r for r in rs if r["base"] and r["base"]["gate"]
                and r["base"]["axis_ok"] and not r["base"]["trio"]]
        print(f"\n=== {lbl} — 重ね買い（三連単プランのみ・n={len(base):,}）===")
        print(HEAD)
        print(line("現行", kpi([r["base"] for r in base], nd)))
        for nm in ("Mx2", "Mx4", "Nx2", "Nx4"):
            pairs = [(r["base"], r["ov"].get(nm) or r["base"]) for r in base]
            recs = [p[1] for p in pairs]
            ap = sum(1 for r in base if nm in r["ov"]) / len(base) * 100
            print(line(f"{nm} (適用{ap:.0f}%)", kpi(recs, nd)))
            res = boot2(_mk([p[0] for p in pairs]), _mk(recs))
            print("      " + "  ".join(
                f"Δ{k} {v[0]:+.2f} [{v[1]:+.2f},{v[2]:+.2f}]"
                for k, v in res.items()))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "grid"
    if cmd == "grid":
        grid(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "cover":
        cover()
    elif cmd == "duel":
        tf = None
        args = sys.argv[2:]
        if args and args[-1].startswith("T="):
            tf = args[-1][2:]; args = args[:-1]
        duel(args[0], *args[1:], tfilter=tf)
    elif cmd == "ov":
        ov()
