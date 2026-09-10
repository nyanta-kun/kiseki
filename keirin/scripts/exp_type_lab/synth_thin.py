#!/usr/bin/env python3
"""合成オッズ × 的中確率(τ) の2次元で商品を間引く案の検証（2026-09-10・ユーザー依頼）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/synth_thin.py <章>

章: band / tau2d / sweep / control / breakeven / oneshot / daily
台: /tmp/synth_thin_rows.pkl（`synth_thin_build.py`）
"""
from __future__ import annotations

import pickle
import random
import sys
from collections import defaultdict
from statistics import median, quantiles

ROWS = pickle.load(open("/tmp/synth_thin_rows.pkl", "rb"))
ONESHOT = {"A_ana", "F_sign"}
WINS = (("探索 2024-07〜2025-12", "explore"), ("確認 2026-01〜08", "confirm"))


def win_rows(w):
    return [r for r in ROWS if r["win"] == w]


def ndays(w):
    return len({r["date"] for r in win_rows(w)})


def synth(r):
    return r["mean"] / 10000.0


def summ(recs, nd):
    if not recs:
        return dict(n=0)
    inv = sum(r["inv"] for r in recs)
    pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    shown = [r for r in hits if r["pay"] >= r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    return dict(n=len(recs), perday=len(recs) / nd,
                k=sum(r["k"] for r in recs) / len(recs),
                hit=len(hits) / len(recs) * 100,
                shown=len(shown) / len(recs) * 100,
                med_pay=median(pays) if pays else 0.0,
                med_synth=median([synth(r) for r in recs]),
                big=sum(1 for p in pays if p >= 100_000) / nd,
                roi=pay / inv * 100 if inv else 0.0,
                tau=sum(r["tau"] for r in recs) / len(recs))


HEAD = ("  {:30s} {:>6s} {:>5s} {:>6s} {:>8s} {:>9s} {:>7s} {:>8s} {:>6s}"
        .format("腕", "件/日", "点数", "τ平均", "表示的中%", "払戻中央", "合成中央",
                "10万+/日", "ROI%"))


def line(name, s):
    if not s.get("n"):
        return f"  {name:30s}  (該当なし)"
    return (f"  {name:30s} {s['perday']:6.2f} {s['k']:5.2f} {s['tau']:6.3f}"
            f" {s['shown']:8.2f} {s['med_pay']:9,.0f} {s['med_synth']:7.2f}"
            f" {s['big']:8.3f} {s['roi']:6.1f}")


def qcut(vals, n):
    """分位の境界（n 分位）。"""
    return quantiles(sorted(vals), n=n, method="inclusive")


# ── 章1: 合成オッズ帯（台の再現） ────────────────────────────────────────
BANDS = [(2.0, 2.5), (2.5, 3.0), (3.0, 4.0), (4.0, 6.0), (6.0, 10.0), (10.0, 1e9)]


def band():
    for label, w in WINS:
        rs, nd = win_rows(w), ndays(w)
        print(f"\n=== {label}  n={len(rs):,} / {nd}日 ===")
        print(HEAD)
        print(line("全体", summ(rs, nd)))
        for lo, hi in BANDS:
            sub = [r for r in rs if lo <= synth(r) < hi]
            nm = f"合成 {lo:.1f}〜{hi:.1f}" if hi < 1e8 else f"合成 {lo:.0f}〜"
            print(line(nm, summ(sub, nd)))
        print("  --- 一撃商品(A_ana/F_sign)を除く ---")
        rs2 = [r for r in rs if r["plan"] not in ONESHOT]
        print(line("当てにいく商品のみ", summ(rs2, nd)))
        for lo, hi in BANDS:
            sub = [r for r in rs2 if lo <= synth(r) < hi]
            nm = f"  合成 {lo:.1f}〜{hi:.1f}" if hi < 1e8 else f"  合成 {lo:.0f}〜"
            print(line(nm, summ(sub, nd)))


# ── 章2: τ × 合成帯の2次元 ───────────────────────────────────────────────
def tau2d():
    for key in ("tau", "tau_cal"):
        print(f"\n############ τ の種類: {key} ############")
        for label, w in WINS:
            rs, nd = win_rows(w), ndays(w)
            rs = [r for r in rs if r["plan"] not in ONESHOT]
            print(f"\n=== {label}（当てにいく商品のみ）n={len(rs):,} / {nd}日 ===")
            for lo, hi in BANDS[:-1]:
                sub = [r for r in rs if lo <= synth(r) < hi]
                if len(sub) < 200:
                    continue
                qs = qcut([r[key] for r in sub], 5)
                print(f"\n  合成 {lo:.1f}〜{hi:.1f}  n={len(sub):,}  "
                      f"{key} 五分位境界 " + " ".join(f"{q:.3f}" for q in qs))
                print(HEAD)
                edges = [-1e9] + qs + [1e9]
                for j in range(5):
                    q = [r for r in sub if edges[j] <= r[key] < edges[j + 1]]
                    print(line(f"  Q{j+1}", summ(q, nd)))
            # 帯を問わない τ 五分位（参考）
            qs = qcut([r[key] for r in rs], 5)
            print(f"\n  【帯を問わない {key} 五分位】境界 " + " ".join(f"{q:.3f}" for q in qs))
            print(HEAD)
            edges = [-1e9] + qs + [1e9]
            for j in range(5):
                q = [r for r in rs if edges[j] <= r[key] < edges[j + 1]]
                print(line(f"  Q{j+1}", summ(q, nd)))


# ── 章3: (X, Y) の掃引 ──────────────────────────────────────────────────
XS = (2.0, 2.25, 2.5, 3.0)
PS = (10, 20, 30, 40, 50)


def _tau_edges(rs, key, lo, hi, p):
    """帯 [lo,hi) の中の τ 下位 p% の境界。"""
    sub = [r[key] for r in rs if lo <= synth(r) < hi]
    if len(sub) < 50:
        return None
    sub.sort()
    return sub[int(len(sub) * p / 100)]


def _apply(rs, key, x, p, cut_oneshot=False):
    """合成 < x かつ τ が帯内下位 p% の商品を落とす。残った行を返す。"""
    if x <= 2.0:
        return list(rs)
    pool = rs if cut_oneshot else [r for r in rs if r["plan"] not in ONESHOT]
    thr = _tau_edges(pool, key, 2.0, x, p)
    if thr is None:
        return list(rs)
    out = []
    for r in rs:
        if not cut_oneshot and r["plan"] in ONESHOT:
            out.append(r)
            continue
        if 2.0 <= synth(r) < x and r[key] < thr:
            continue
        out.append(r)
    return out


def _daily(recs, nd_days):
    """日次の表示的中の分布。"""
    per = defaultdict(list)
    for r in recs:
        per[r["date"]].append(r)
    vals, zero = [], 0
    for d in nd_days:
        v = per.get(d, [])
        if not v:
            continue
        s = sum(1 for r in v if r["pay"] >= r["inv"] and r["pay"] > 0) / len(v) * 100
        vals.append(s)
        zero += s == 0
    vals.sort()
    q = quantiles(vals, n=4, method="inclusive") if len(vals) > 3 else [0, 0, 0]
    return dict(med=median(vals), q1=q[0], q3=q[2], zero=zero / len(vals) * 100)


def sweep():
    for key in ("tau", "tau_cal"):
        print(f"\n############ τ の種類: {key} ############")
        for label, w in WINS:
            rs, nd = win_rows(w), ndays(w)
            days = sorted({r["date"] for r in rs})
            print(f"\n=== {label}  n={len(rs):,} / {nd}日 ===")
            print(HEAD + "  " + "{:>7s} {:>7s} {:>7s} {:>7s}".format(
                "日中央", "日Q1", "日Q3", "全外れ日%"))
            base = summ(rs, nd)
            db = _daily(rs, days)
            print(line("① 現行", base) + "  "
                  f"{db['med']:7.2f} {db['q1']:7.2f} {db['q3']:7.2f} {db['zero']:9.2f}")
            for x in XS[1:]:
                for p in PS:
                    kept = _apply(rs, key, x, p)
                    s = summ(kept, nd)
                    d = _daily(kept, days)
                    print(line(f"合成<{x} かつ τ下位{p}% 除外", s) + "  "
                          f"{d['med']:7.2f} {d['q1']:7.2f} {d['q3']:7.2f} {d['zero']:9.2f}")




# ── 章2b: 帯の中で τ が的中を分離するか（AUC） ─────────────────────────
def _auc(vals, lab):
    """Mann-Whitney AUC（lab=True を高く並べられるか）。"""
    pos = [v for v, l in zip(vals, lab) if l]
    neg = [v for v, l in zip(vals, lab) if not l]
    if not pos or not neg:
        return float("nan")
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    rank, i = [0.0] * len(vals), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            rank[order[t]] = r
        i = j + 1
    sp = sum(rank[i] for i in range(len(vals)) if lab[i])
    n1, n0 = len(pos), len(neg)
    return (sp - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def auc():
    rnd = random.Random(20260910)
    for key in ("tau", "tau_cal"):
        print(f"\n############ {key} が帯の中で表示的中を分離するか（AUC・bootstrap 500） ############")
        print("  {:18s} {:>6s} {:>18s} {:>6s} {:>18s}".format(
            "帯", "n(探索)", "AUC 探索 [95%CI]", "n(確認)", "AUC 確認 [95%CI]"))
        for lo, hi in BANDS:
            cells = []
            for _, w in WINS:
                rs = [r for r in win_rows(w) if r["plan"] not in ONESHOT
                      and lo <= synth(r) < hi]
                if len(rs) < 100:
                    cells.append((len(rs), None, None, None))
                    continue
                v = [r[key] for r in rs]
                l = [r["pay"] >= r["inv"] and r["pay"] > 0 for r in rs]
                a = _auc(v, l)
                bs = []
                for _ in range(500):
                    idx = [rnd.randrange(len(rs)) for _ in range(len(rs))]
                    bs.append(_auc([v[i] for i in idx], [l[i] for i in idx]))
                bs = sorted(x for x in bs if x == x)
                cells.append((len(rs), a, bs[int(0.025 * len(bs))], bs[int(0.975 * len(bs))]))
            nm = f"合成 {lo:.1f}〜{hi:.1f}" if hi < 1e8 else f"合成 {lo:.0f}〜"
            out = f"  {nm:18s}"
            for n, a, lo95, hi95 in cells:
                out += (f" {n:6,d} {'  ---':>18s}" if a is None
                        else f" {n:6,d} {a:.3f} [{lo95:.3f},{hi95:.3f}]")
            print(out)
        # 全帯まとめ
        out = f"  {'全帯（帯を問わず）':18s}"
        for _, w in WINS:
            rs = [r for r in win_rows(w) if r["plan"] not in ONESHOT]
            v = [r[key] for r in rs]
            l = [r["pay"] >= r["inv"] and r["pay"] > 0 for r in rs]
            out += f" {len(rs):6,d} {_auc(v, l):.3f} {'':>12s}"
        print(out)


# ── 章4: 無作為対照（20seed）と、Δ表示的中の CI ──────────────────────────
def _shown(recs):
    return sum(1 for r in recs if r["pay"] > 0 and r["pay"] >= r["inv"]) / len(recs) * 100


def _boot_delta(base, kept, rnd, n=800):
    """日単位のブロック bootstrap で Δ表示的中(kept − base) の 95%CI。"""
    bd, kd = defaultdict(list), defaultdict(list)
    for r in base:
        bd[r["date"]].append(r)
    for r in kept:
        kd[r["date"]].append(r)
    days = sorted(bd)
    out = []
    for _ in range(n):
        pick = [days[rnd.randrange(len(days))] for _ in range(len(days))]
        b = [r for d in pick for r in bd[d]]
        k = [r for d in pick for r in kd.get(d, [])]
        if not b or not k:
            continue
        out.append(_shown(k) - _shown(b))
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out))]


CTRL_ARMS = ((2.5, 30), (2.5, 50), (3.0, 30), (3.0, 50))


def control():
    rnd = random.Random(20260910)
    for key in ("tau", "tau_cal"):
        print(f"\n############ τ の種類: {key} ############")
        for label, w in WINS:
            rs, nd = win_rows(w), ndays(w)
            print(f"\n=== {label}  n={len(rs):,} / {nd}日 ===")
            base = summ(rs, nd)
            print(HEAD + "  {:>18s} {:>10s}".format("Δ表示的中 95%CI", "対照20本"))
            print(line("① 現行", base))
            for x, p in CTRL_ARMS:
                kept = _apply(rs, key, x, p)
                s = summ(kept, nd)
                lo95, hi95 = _boot_delta(rs, kept, rnd)
                # 無作為対照: 同じ母集団（合成<x・一撃商品を除く）から同数を無作為に落とす
                pool = [i for i, r in enumerate(rs)
                        if r["plan"] not in ONESHOT and 2.0 <= synth(r) < x]
                n_drop = len(rs) - len(kept)
                wins_ = 0
                for seed in range(20):
                    rg = random.Random(1000 + seed)
                    drop = set(rg.sample(pool, min(n_drop, len(pool))))
                    ct = [r for i, r in enumerate(rs) if i not in drop]
                    wins_ += s["shown"] > _shown(ct)
                print(line(f"合成<{x} かつ τ下位{p}% 除外", s)
                      + f"  [{lo95:+6.2f},{hi95:+6.2f}] {wins_:>7d}/20")


# ── 章5: 「間引く」の代わりに使える既知のダイヤル（τ 全体床） ────────────
def alt():
    for label, w in WINS:
        rs, nd = win_rows(w), ndays(w)
        print(f"\n=== {label}  n={len(rs):,} / {nd}日 ===")
        print(HEAD)
        print(line("① 現行", summ(rs, nd)))
        keep = [r for r in rs if r["plan"] not in ONESHOT]
        one = [r for r in rs if r["plan"] in ONESHOT]
        for p in (10, 20, 30, 40, 50):
            v = sorted(r["tau"] for r in keep)
            thr = v[int(len(v) * p / 100)]
            kept = [r for r in keep if r["tau"] >= thr] + one
            print(line(f"② τ 下位{p}% 除外（帯を問わない・一撃は残す）", summ(kept, nd)))
        for lo in (2.5, 3.0, 4.0):
            kept = [r for r in keep if synth(r) >= lo] + one
            print(line(f"③ 合成 {lo} 未満を丸ごと除外（一撃は残す）", summ(kept, nd)))
        for p in (10, 30):
            v = sorted(r["tau"] for r in rs)
            thr = v[int(len(v) * p / 100)]
            print(line(f"④ τ 下位{p}% 除外（一撃も対象・参考）",
                       summ([r for r in rs if r["tau"] >= thr], nd)))


# ── 章6: 損益分岐（帯ごとに必要な的中率と実際） ───────────────────────
def breakeven():
    """ROI = 的中率 × 的中したときの平均払戻倍率。**必要的中率はその倍率の逆数。**

    🔴 予測合成オッズ（= mean/10000）は「予測どおりなら1件あたり何倍返ってくるか」で、
       実際に当たったときの倍率とは別物。両方出して差を見る。
    """
    for label, w in WINS:
        rs, nd = win_rows(w), ndays(w)
        print(f"\n=== {label} ===")
        print("  {:16s} {:>6s} {:>8s} {:>10s} {:>9s} {:>9s} {:>9s} {:>8s} {:>7s}".format(
            "帯", "n", "予測合成", "実現倍率", "必要的中%", "実的中%", "表示的中%",
            "差(pt)", "ROI%"))
        for lo, hi in BANDS:
            sub = [r for r in rs if lo <= synth(r) < hi]
            if not sub:
                continue
            s = summ(sub, nd)
            hits = [r for r in sub if r["pay"] > 0]
            mult = (sum(r["pay"] for r in hits) / sum(r["inv"] for r in hits)) if hits else 0.0
            need = 100.0 / mult if mult else float("nan")
            nm = f"合成 {lo:.1f}〜{hi:.1f}" if hi < 1e8 else f"合成 {lo:.0f}〜"
            print(f"  {nm:16s} {s['n']:6,d} {s['med_synth']:8.2f} {mult:10.2f}"
                  f" {need:9.2f} {s['hit']:9.2f} {s['shown']:9.2f}"
                  f" {s['hit']-need:8.2f} {s['roi']:7.1f}")


# ── 章7: 一撃商品も一緒に間引いたら何を失うか ────────────────────────
def oneshot():
    for key in ("tau",):
        for label, w in WINS:
            rs, nd = win_rows(w), ndays(w)
            print(f"\n=== {label}  n={len(rs):,} / {nd}日 ===")
            print(HEAD)
            print(line("① 現行", summ(rs, nd)))
            print(line("参考: 一撃商品(A_ana/F_sign)のみ",
                       summ([r for r in rs if r["plan"] in ONESHOT], nd)))
            for x, p in ((3.0, 50),):
                print(line(f"合成<{x} τ下位{p}% 除外（一撃は残す）",
                           summ(_apply(rs, key, x, p), nd)))
                print(line(f"合成<{x} τ下位{p}% 除外（一撃も対象）",
                           summ(_apply(rs, key, x, p, cut_oneshot=True), nd)))
            print(line("一撃商品を丸ごと外す",
                       summ([r for r in rs if r["plan"] not in ONESHOT], nd)))


# ── 章8: 日次の経済（1日の投資・払戻・損益／看板の出た日と出ない日） ──────
def daily():
    for label, w in WINS:
        rs, nd = win_rows(w), ndays(w)
        per = defaultdict(list)
        for r in rs:
            per[r["date"]].append(r)
        recs = []
        for d, v in per.items():
            inv = sum(r["inv"] for r in v)
            pay = sum(r["pay"] for r in v)
            big = sum(1 for r in v if r["pay"] >= 100_000)
            recs.append(dict(d=d, n=len(v), inv=inv, pay=pay, pl=pay - inv, big=big,
                             shown=_shown(v)))
        recs.sort(key=lambda r: r["pl"])
        pls = [r["pl"] for r in recs]
        print(f"\n=== {label}  {nd}日 ===")
        print(f"  1日あたり: 件数 {sum(r['n'] for r in recs)/nd:.2f} / "
              f"投資 {sum(r['inv'] for r in recs)/nd:,.0f}円 / "
              f"払戻 {sum(r['pay'] for r in recs)/nd:,.0f}円 / "
              f"損益 {sum(pls)/nd:+,.0f}円")
        q = quantiles(sorted(pls), n=4, method="inclusive")
        print(f"  日次損益: 最悪 {min(pls):+,.0f} / Q1 {q[0]:+,.0f} / 中央 {q[1]:+,.0f}"
              f" / Q3 {q[2]:+,.0f} / 最良 {max(pls):+,.0f}")
        print(f"  黒字の日 {sum(1 for x in pls if x > 0)/len(pls)*100:.1f}%"
              f" / 表示的中0%の日 {sum(1 for r in recs if r['shown']==0)/len(recs)*100:.1f}%")
        for cond, nm in ((lambda r: r["big"] > 0, "10万+が出た日"),
                         (lambda r: r["big"] == 0, "10万+が無い日")):
            v = [r for r in recs if cond(r)]
            if not v:
                continue
            pl = [r["pl"] for r in v]
            print(f"  {nm:14s} {len(v):4d}日 ({len(v)/nd*100:5.1f}%)  "
                  f"損益 平均 {sum(pl)/len(pl):+9,.0f}円 / 中央 {median(pl):+9,.0f}円  "
                  f"ROI {sum(r['pay'] for r in v)/sum(r['inv'] for r in v)*100:5.1f}%")
        # 「多数出して当たらない日」の実態
        v = [r for r in recs if r["shown"] * r["n"] / 100 < 3]
        print(f"  表示的中3件未満の日 {len(v)}日 ({len(v)/nd*100:.1f}%)  "
              f"損益中央 {median([r['pl'] for r in v]):+,.0f}円")


# ── 章9: 向きを逆にした腕（合成が「高い」×τ が低い） ──────────────────────
def _apply_hi(rs, key, lo, hi, p):
    pool = [r[key] for r in rs if r["plan"] not in ONESHOT and lo <= synth(r) < hi]
    if len(pool) < 50:
        return list(rs)
    pool.sort()
    thr = pool[int(len(pool) * p / 100)]
    out = []
    for r in rs:
        if (r["plan"] not in ONESHOT and lo <= synth(r) < hi and r[key] < thr):
            continue
        out.append(r)
    return out


def hi_arm():
    rnd = random.Random(20260910)
    for key in ("tau",):
        for label, w in WINS:
            rs, nd = win_rows(w), ndays(w)
            print(f"\n=== {label}  n={len(rs):,} / {nd}日 ===")
            print(HEAD + "  {:>18s} {:>10s}".format("Δ表示的中 95%CI", "対照20本"))
            print(line("① 現行", summ(rs, nd)))
            for lo, hi, p in ((4.0, 10.0, 20), (4.0, 10.0, 30), (4.0, 10.0, 40),
                              (3.0, 10.0, 20), (3.0, 10.0, 30)):
                kept = _apply_hi(rs, key, lo, hi, p)
                s = summ(kept, nd)
                lo95, hi95 = _boot_delta(rs, kept, rnd)
                pool = [i for i, r in enumerate(rs)
                        if r["plan"] not in ONESHOT and lo <= synth(r) < hi]
                n_drop = len(rs) - len(kept)
                wins_ = 0
                for seed in range(20):
                    rg = random.Random(2000 + seed)
                    drop = set(rg.sample(pool, min(n_drop, len(pool))))
                    wins_ += s["shown"] > _shown([r for i, r in enumerate(rs) if i not in drop])
                print(line(f"合成{lo}〜{hi} かつ τ下位{p}% 除外", s)
                      + f"  [{lo95:+6.2f},{hi95:+6.2f}] {wins_:>7d}/20")


# ── 章10: 日次上限の下で測る（間引いた枠は次順位のレースが埋める） ────────
def _gate_mod():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2].parent / "backend/src/services/keirin_type_lab_gate.py"
    spec = importlib.util.spec_from_file_location("keirin_type_lab_gate", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _with_rp_sd(rows):
    """`miss_anatomy_rows.pkl` から rp_sd を貰う（同じ i で作られている）。"""
    src = pickle.load(open("/tmp/miss_anatomy_rows.pkl", "rb"))
    rp = {(r["win"], r["i"]): r["rp_sd"] for r in src}
    for r in rows:
        r["rp_sd"] = rp.get((r["win"], r["i"]))
    return rows


def _cap_select(rows, G, denom=None):
    """本番の入稿ループを台の上で再現する（軸信頼ゲート → 上限 → 優先順位）。"""
    per, dper = defaultdict(list), defaultdict(list)
    for r in rows:
        per[r["date"]].append(r)
    for r in (denom if denom is not None else rows):
        dper[r["date"]].append(r)
    out = []
    for d, v in per.items():
        # 分母は「判定できたレース」＝入稿ループで judged=True を返す行。
        # 🔴 ゲートを**入稿ループ側**に置くと分母は減らない（denom を渡す）。
        #    生成側で落とすと行そのものが消えるので分母も減る（denom=None）。
        n_judged = len([r for r in dper[d]
                        if not G.daily_cap_exempt(r["rtype"], None)])
        passed = [r for r in v if G.passes_axis_gate(r["plan"], r["axis"], 7)]
        ex = [r for r in passed if G.daily_cap_exempt(r["rtype"], None)]
        rest = [r for r in passed if r not in ex]
        rest.sort(key=lambda r: -G.cap_priority(r["plan"], r["axis"], r.get("rp_sd")))
        cap = max(1, int(n_judged * 0.5)) if n_judged else None
        out += ex + (rest[:cap] if cap is not None else rest)
    return out


def cap_sim():
    G = _gate_mod()
    rnd = random.Random(20260910)
    for key in ("tau",):
        for label, w in WINS:
            rs, nd = _with_rp_sd(win_rows(w)), ndays(w)
            base = _cap_select(rs, G)
            print(f"\n=== {label}  台 {len(rs):,}件 → 上限通過 {len(base):,}件 / {nd}日 ===")
            print(HEAD + "  {:>18s}".format("Δ表示的中 95%CI"))
            print(line("① 現行（上限あり）", summ(base, nd)))
            for x, p in ((2.5, 30), (2.5, 50), (3.0, 30), (3.0, 50)):
                thinned = _apply(rs, key, x, p)
                for nm, dn in (("入稿ループ側(分母不変)", rs), ("生成側(分母も減る)", None)):
                    kept = _cap_select(thinned, G, dn)
                    s = summ(kept, nd)
                    lo95, hi95 = _boot_delta(base, kept, rnd)
                    print(line(f"合成<{x} τ下位{p}% 除外 / {nm}", s)
                          + f"  [{lo95:+6.2f},{hi95:+6.2f}]")
            for lo, hi, p in ((4.0, 10.0, 30), (3.0, 10.0, 30)):
                thinned = _apply_hi(rs, key, lo, hi, p)
                for nm, dn in (("入稿ループ側(分母不変)", rs), ("生成側(分母も減る)", None)):
                    kept = _cap_select(thinned, G, dn)
                    s = summ(kept, nd)
                    lo95, hi95 = _boot_delta(base, kept, rnd)
                    print(line(f"合成{lo}〜{hi} τ下位{p}% 除外 / {nm}", s)
                          + f"  [{lo95:+6.2f},{hi95:+6.2f}]")


if __name__ == "__main__":
    globals()[sys.argv[1]]()
