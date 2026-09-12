#!/usr/bin/env python3
"""帯 L × 点数 k をレースごとに条件付きで**同時に逆向きへ**動かす（2026-09-10）。

台: `band_by_race_build.py` → /tmp/band_by_race_rows.pkl

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/band_by_race.py A|B|C
"""
from __future__ import annotations

import pickle
import sys
from collections import defaultdict

import numpy as np

D = pickle.load(open("/tmp/band_by_race_rows.pkl", "rb"))
ROWS, KEYS, ARMS = D["rows"], D["keys"], D["arms"]
PROD = {"C": (15.0, 12), "E": (30.0, 14)}
GRID = {t: (sorted({L for L, _ in KEYS[t]}), sorted({k for _, k in KEYS[t]})) for t in ("C", "E")}
AXIS_GATE = {"E_hit": 1.245}          # C_hit は exempt（本番どおり）
W = ("confirm", "explore")
WLAB = {"confirm": "確認 2026-01〜08（本番相当）", "explore": "探索 2024-07〜2025-12"}

for r in ROWS:
    r["axis_ok"] = r["axis"] >= AXIS_GATE.get(r["plan"], 0.0)

BYW = {w: [r for r in ROWS if r["win"] == w] for w in W}
NDAYS = {w: len({r["date"] for r in BYW[w]}) for w in W}


def sub(tl: str, w: str, axis_gate: bool = True):
    return [r for r in BYW[w] if r["type"] == tl and (r["axis_ok"] or not axis_gate)]


def arm(r, L, k):
    """(L,k) の腕。組めなければ None。dict(k,inv,pay,mean,gate)。"""
    j = KEYS[r["type"]].index((L, k))
    a = ARMS[r["type"]][r["arm_i"]][j]
    if not np.isfinite(a[0]):
        return None
    return dict(k=float(a[0]), inv=float(a[1]), pay=float(a[2]),
                mean=float(a[3]), gate=bool(a[4]))


def agg(recs, nd):
    if not recs:
        return dict(n=0, perday=0, k=0, hit=0, shown=0, gami=0, med=0, roi=0, big=0,
                    med_mean=0, inv=0)
    inv = sum(x["inv"] for x in recs)
    pay = sum(x["pay"] for x in recs)
    pays = sorted(x["pay"] for x in recs if x["pay"] > 0)
    nh = len(pays)
    ns = sum(1 for x in recs if x["pay"] >= x["inv"])
    return dict(n=len(recs), perday=len(recs) / nd,
                k=float(np.mean([x["k"] for x in recs])),
                hit=nh / len(recs) * 100, shown=ns / len(recs) * 100,
                gami=(nh - ns) / nh * 100 if nh else 0.0,
                med=float(np.median(pays)) if pays else 0.0,
                roi=pay / inv * 100 if inv else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / nd,
                med_mean=float(np.median([x["mean"] for x in recs])),
                inv=inv / nd)


HEAD = ("  {:34s} {:>6s} {:>5s} {:>6s} {:>8s} {:>6s} {:>8s} {:>7s} {:>7s}"
        .format("腕", "件/日", "点数", "的中%", "表示的中%", "ガミ%", "払戻中央", "10万+/日", "ROI%"))


def line(name, s):
    if not s["n"]:
        return f"  {name:34s}  (該当なし)"
    return (f"  {name:34s} {s['perday']:6.2f} {s['k']:5.1f} {s['hit']:6.2f} {s['shown']:8.2f}"
            f" {s['gami']:6.1f} {s['med']:8,.0f} {s['big']:7.3f} {s['roi']:7.1f}")


def boot(a, b, iters=2000, seed=0):
    """対応比較（同一レース）の Δ表示的中・ΔROI の 95%CI。a - b。"""
    rng = np.random.default_rng(seed)
    A = np.array([x["pay"] >= x["inv"] for x in a], float)
    B = np.array([x["pay"] >= x["inv"] for x in b], float)
    ap = np.array([x["pay"] for x in a]); ai = np.array([x["inv"] for x in a])
    bp = np.array([x["pay"] for x in b]); bi = np.array([x["inv"] for x in b])
    n = len(A); ds, dr = [], []
    for _ in range(iters):
        j = rng.integers(0, n, n)
        ds.append((A[j].mean() - B[j].mean()) * 100)
        dr.append(ap[j].sum() / ai[j].sum() * 100 - bp[j].sum() / bi[j].sum() * 100)
    q = lambda v: (float(np.mean(v)), float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)))
    return q(ds), q(dr)


def auc(y, s):
    y = np.asarray(y, float); s = np.asarray(s, float)
    m = np.isfinite(s)
    y, s = y[m], s[m]
    if y.sum() == 0 or y.sum() == len(y):
        return float("nan")
    r = np.argsort(np.argsort(s)) + 1
    n1 = y.sum(); n0 = len(y) - n1
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


CANDS = ["hs_trio", "q_m3", "sp5", "sp12", "sp14", "med_po5", "med_po12", "med_po14",
         "sig12", "sig14", "axis", "gap", "arare", "pw_ent"]


def section_a():
    print("\n" + "=" * 118)
    print("A. 条件量 — 「決着がどの配当帯に落ちるか」を朝の情報だけで読めるか")
    print("=" * 118)
    for tl in ("C", "E"):
        L0 = PROD[tl][0]
        for w in W:
            rs = sub(tl, w)
            y_under = np.array([1.0 if (np.isfinite(r["po_win"]) and r["po_win"] < L0) else 0.0
                                for r in rs])
            y_jun = np.array([1.0 if r["jundo"] else 0.0 for r in rs])
            print(f"\n--- 型{tl} {WLAB[w]}  n={len(rs):,} "
                  f"（決着が現行帯 {L0:.0f}倍未満 = {y_under.mean()*100:.1f}% / "
                  f"順当決着 {y_jun.mean()*100:.1f}%）")
            print(f"  {'条件量':10s} {'AUC(帯未満)':>11s} {'AUC(順当)':>10s} "
                  f"{'決着予測ｵｯｽﾞ中央 D1':>18s} {'D5':>8s} {'D10':>8s} "
                  f"{'帯未満% D1':>10s} {'D5':>7s} {'D10':>7s}")
            for c in CANDS:
                v = np.array([r[c] for r in rs], float)
                if not np.isfinite(v).any():
                    continue
                a1, a2 = auc(y_under, v), auc(y_jun, v)
                # 十分位（欠測は除く）
                m = np.isfinite(v)
                q = np.quantile(v[m], np.linspace(0, 1, 11))
                dec = np.clip(np.searchsorted(q[1:-1], v, side="right"), 0, 9)
                pw = np.array([r["po_win"] for r in rs], float)
                med = {}
                und = {}
                for d in (0, 4, 9):
                    s = m & (dec == d)
                    med[d] = float(np.nanmedian(pw[s])) if s.any() else float("nan")
                    und[d] = float(y_under[s].mean() * 100) if s.any() else float("nan")
                print(f"  {c:10s} {a1:11.3f} {a2:10.3f} {med[0]:18.1f} {med[4]:8.1f} "
                      f"{med[9]:8.1f} {und[0]:9.1f}% {und[4]:6.1f}% {und[9]:6.1f}%")




# ═══════════════════════════════════════════════════════════════════════════
# B. 2次元（帯 L × 点数 k）を条件で振り分ける
#
# 🔴 母集団は「**現行が入稿ゲートを通るレース**」に固定し、腕がゲートに落ちたら
#    現行へ戻す（`GATE_FALLBACK` と同じ設計）。これで件数が1件も動かず対応比較になる。
# 🔴 分位の境界は**探索窓**で作り、両窓に同じ値を当てる。
# ═══════════════════════════════════════════════════════════════════════════

def pop(tl, w):
    return [r for r in sub(tl, w) if r["base"]["gate"]]


def thresholds(tl, var, n_grp):
    v = np.array([r[var] for r in pop(tl, "explore")], float)
    v = v[np.isfinite(v)]
    return list(np.quantile(v, np.linspace(0, 1, n_grp + 1)[1:-1]))


def grp_of(r, var, thr):
    x = r[var]
    if not np.isfinite(x):
        return len(thr)          # 欠測は最上位群（=現行のまま）へ倒す
    return int(np.searchsorted(thr, x, side="right"))


def apply_rule(rs, var, thr, mapping):
    """mapping: {群index: (L,k) or None(=現行)} -> レコード列（現行へのフォールバック込み）。"""
    out, fired = [], 0
    for r in rs:
        g = grp_of(r, var, thr)
        lk = mapping.get(g)
        if lk is not None:
            a = arm(r, *lk)
            if a and a["gate"]:
                out.append(a); fired += 1; continue
        out.append(r["base"])
    return out, fired


def section_b(var="sig14", n_grp=3):
    print("\n" + "=" * 118)
    print(f"B-1. 群ごとの (L,k) 格子 — 条件量 {var}・{n_grp}分位（境界は探索窓）")
    print("=" * 118)
    best = {}
    for tl in ("C", "E"):
        Ls, ks = GRID[tl]
        thr = thresholds(tl, var, n_grp)
        print(f"\n### 型{tl}  現行 (L={PROD[tl][0]:.0f}, k={PROD[tl][1]})  "
              f"境界={[round(t,4) for t in thr]}")
        for g in range(n_grp):
            lab = ["低（=高配当想定）", "中", "高（=低配当想定）"][g] if n_grp == 3 else f"群{g}"
            print(f"\n  -- 群{g} {lab} --")
            cell = {}
            for w in W:
                rs = [r for r in pop(tl, w) if grp_of(r, var, thr) == g]
                b = agg([r["base"] for r in rs], NDAYS[w])
                cell[(w, "base")] = b
                for L in Ls:
                    for k in ks:
                        rec, fired = apply_rule(rs, var, thr, {g: (L, k)})
                        s = agg(rec, NDAYS[w])
                        s["fire"] = fired / len(rs) * 100 if rs else 0
                        cell[(w, (L, k))] = s
            # 表示（確認窓の表示的中を主指標に、探索窓を併記）
            print(f"      {'k=':>3s}" + "".join(f"{k:>15d}" for k in ks))
            for L in Ls:
                row = f"    L={L:5.0f}"
                for k in ks:
                    c, e = cell[("confirm", (L, k))], cell[("explore", (L, k))]
                    row += f"  {c['shown']:5.2f}/{e['shown']:5.2f}"
                print(row)
            bb, be = cell[("confirm", "base")], cell[("explore", "base")]
            print(f"    現行            {bb['shown']:5.2f}/{be['shown']:5.2f}"
                  f"   (n={bb['n']}/{be['n']})")
            # 両窓の平均で最良を拾う（採否ではなく方向の確認）
            cands = [(L, k) for L in Ls for k in ks]
            sc = {c: (cell[("confirm", c)]["shown"] + cell[("explore", c)]["shown"]) / 2
                  for c in cands}
            top = sorted(sc, key=lambda c: -sc[c])[:5]
            best[(tl, g)] = top
            print(f"    両窓平均の上位5: " + " / ".join(f"L{c[0]:.0f}k{c[1]}({sc[c]:.2f})" for c in top))
            # 探索窓だけで選んだ最良（honest な選び方）
            se = {c: cell[("explore", c)]["shown"] for c in cands}
            e_best = max(se, key=lambda c: se[c])
            print(f"    探索窓だけで選ぶと L{e_best[0]:.0f} k{e_best[1]}"
                  f" → 確認窓 {cell[('confirm', e_best)]['shown']:.2f}"
                  f"（現行 {bb['shown']:.2f}・発火 {cell[('confirm', e_best)]['fire']:.0f}%）")
    return best




# ═══════════════════════════════════════════════════════════════════════════
# C. 腕の突き合わせ — 必須の対照つき
#
# 対照は「**群ラベルを無作為に入れ替える**」20 seed。(L,k) の混ぜ方は同じで
# 割り当てだけを壊すので、「条件量が情報を持っているか」を直接測れる。
# ═══════════════════════════════════════════════════════════════════════════

def rule_recs(tl, w, var, thr, mapping):
    rs = pop(tl, w)
    return apply_rule(rs, var, thr, mapping)


def perm_control(tl, w, var, thr, mapping, seeds=20):
    rs = pop(tl, w)
    g = np.array([grp_of(r, var, thr) for r in rs])
    out = []
    for s in range(seeds):
        rng = np.random.default_rng(s)
        gp = rng.permutation(g)
        rec = []
        for r, gg in zip(rs, gp):
            lk = mapping.get(int(gg))
            a = arm(r, *lk) if lk is not None else None
            rec.append(a if (a and a["gate"]) else r["base"])
        out.append(agg(rec, NDAYS[w]))
    return out


def section_c(var="sig14"):
    print("\n" + "=" * 118)
    print(f"C. 腕の突き合わせ（条件量 {var}・3分位・境界は探索窓）")
    print("=" * 118)
    THR = {tl: thresholds(tl, var, 3) for tl in ("C", "E")}
    # Σp5 の探索窓 p50（miss_anatomy §5 の腕）
    SP50 = {tl: float(np.percentile([r["sp5"] for r in pop(tl, "explore")], 50))
            for tl in ("C", "E")}

    ARMS_DEF = {
        "C": {
            "現行 L15 k12（＋帯下1点）": None,
            "一律 L10 k6（type_c.md の否定腕）": {0: (10, 6), 1: (10, 6), 2: (10, 6)},
            "一律 L0 k5": {0: (0, 5), 1: (0, 5), 2: (0, 5)},
            "一律 L15 k14": {0: (15, 14), 1: (15, 14), 2: (15, 14)},
            "2次元 探索窓best(15,14)/(15,16)/(10,10)": {0: (15, 14), 1: (15, 16), 2: (10, 10)},
            "ユーザー案 高配当→(25,16)/中現行/低配当→(0,5)": {0: (25, 16), 2: (0, 5)},
            "点数だけ 14/16/10（帯は現行15）": {0: (15, 14), 1: (15, 16), 2: (15, 10)},
        },
        "E": {
            "現行 L30 k14": None,
            "一律 L0 k14（帯撤廃・点数据置）": {0: (0, 14), 1: (0, 14), 2: (0, 14)},
            "一律 L0 k10": {0: (0, 10), 1: (0, 10), 2: (0, 10)},
            "一律 L0 k6": {0: (0, 6), 1: (0, 6), 2: (0, 6)},
            "一律 L15 k14": {0: (15, 14), 1: (15, 14), 2: (15, 14)},
            "2次元 探索窓best(0,14)/(0,10)/(0,6)": {0: (0, 14), 1: (0, 10), 2: (0, 6)},
            "ユーザー案 高配当→(40,18)/中現行/低配当→(0,6)": {0: (40, 18), 2: (0, 6)},
            "点数だけ 18/14/8（帯は現行30）": {0: (30, 18), 1: (30, 14), 2: (30, 8)},
            "帯だけ 30/15/0（点数は現行14）": {0: (30, 14), 1: (15, 14), 2: (0, 14)},
        },
    }
    for tl in ("C", "E"):
        for w in W:
            print(f"\n### 型{tl}  {WLAB[w]}   （母集団＝現行がゲートを通るレース）")
            print(HEAD + "   " + "Δ表示的中 95%CI".rjust(22) + "  " + "対照20seed".rjust(10))
            base_rec = [r["base"] for r in pop(tl, w)]
            print(line("現行（基準）", agg(base_rec, NDAYS[w])))
            for name, mp in ARMS_DEF[tl].items():
                if mp is None:
                    continue
                rec, fired = rule_recs(tl, w, var, THR[tl], mp)
                s = agg(rec, NDAYS[w])
                ds, dr = boot(rec, base_rec)
                ctrl = perm_control(tl, w, var, THR[tl], mp)
                wins = sum(1 for c in ctrl if s["shown"] > c["shown"])
                med = float(np.median([c["shown"] for c in ctrl]))
                print(line(name + f" [発火{fired/max(len(rec),1)*100:.0f}%]", s)
                      + f"  {ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]"
                      + f"  {wins:2d}/20(中央{med:.2f})")
            # miss_anatomy §5 の腕（Σp5 上位半分だけ (0,5)）
            rs = pop(tl, w)
            rec = []
            fired = 0
            for r in rs:
                if r["sp5"] >= SP50[tl]:
                    a = arm(r, 0.0, 5)
                    if a and a["gate"]:
                        rec.append(a); fired += 1; continue
                rec.append(r["base"])
            s = agg(rec, NDAYS[w])
            ds, dr = boot(rec, base_rec)
            print(line(f"§5 Σp5上位半分→(0,5) [発火{fired/len(rs)*100:.0f}%]", s)
                  + f"  {ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]")


def section_d(var="sig14"):
    """維持 / 破壊 / 救済 と、ラインナップ全体への当て込み。"""
    THR = {tl: thresholds(tl, var, 3) for tl in ("C", "E")}
    RULES = {
        "E 2次元 (0,14)/(0,10)/(0,6)": ("E", {0: (0, 14), 1: (0, 10), 2: (0, 6)}),
        "E 一律 (0,10)": ("E", {0: (0, 10), 1: (0, 10), 2: (0, 10)}),
        "E 点数だけ 18/14/8": ("E", {0: (30, 18), 1: (30, 14), 2: (30, 8)}),
        "C 2次元 (15,14)/(15,16)/(10,10)": ("C", {0: (15, 14), 1: (15, 16), 2: (10, 10)}),
    }
    print("\n" + "=" * 118)
    print("D. 維持 / 破壊 / 救済 と、ラインナップ全体")
    print("=" * 118)
    for name, (tl, mp) in RULES.items():
        for w in W:
            rs = pop(tl, w)
            rec, _ = apply_rule(rs, var, THR[tl], mp)
            keep = brk = sav = 0
            for r, a in zip(rs, rec):
                b = r["base"]
                bs, as_ = b["pay"] >= b["inv"], a["pay"] >= a["inv"]
                keep += bs and as_
                brk += bs and not as_
                sav += (not bs) and as_
            print(f"  {name:34s} {WLAB[w][:2]}  維持 {keep:4d} / 破壊 {brk:4d} / 救済 {sav:4d}"
                  f"  （現行の的中 {keep+brk}）")
    # ラインナップ
    print("\n--- ラインナップ全体（他の型は現行のまま・軸信頼ゲート込み）")
    LU = {
        "現行": {},
        "E だけ 2次元 (0,14)/(0,10)/(0,6)": {"E": {0: (0, 14), 1: (0, 10), 2: (0, 6)}},
        "E だけ 一律 (0,10)": {"E": {0: (0, 10), 1: (0, 10), 2: (0, 10)}},
        "E 2次元 + C 2次元": {"E": {0: (0, 14), 1: (0, 10), 2: (0, 6)},
                              "C": {0: (15, 14), 1: (15, 16), 2: (10, 10)}},
    }
    for w in W:
        print(f"\n  [{WLAB[w]}]")
        print(HEAD)
        base_all = None
        for name, rules in LU.items():
            rec = []
            for r in BYW[w]:
                if not r["axis_ok"] or not r["base"]["gate"]:
                    continue
                mp = rules.get(r["type"])
                if mp is not None:
                    g = grp_of(r, var, THR[r["type"]])
                    lk = mp.get(g)
                    if lk is not None:
                        a = arm(r, *lk)
                        if a and a["gate"]:
                            rec.append(a); continue
                rec.append(r["base"])
            s = agg(rec, NDAYS[w])
            if base_all is None:
                base_all = rec
                print(line(name, s))
            else:
                ds, dr = boot(rec, base_all)
                print(line(name, s) + f"  Δ表示的中 {ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]"
                      f"  ΔROI {dr[0]:+5.1f}[{dr[1]:+5.1f},{dr[2]:+5.1f}]")




def section_e(var="sig14"):
    """条件付けそのものの増分（一律腕を基準にした対応比較）と、近傍の頑健性。"""
    THR = {tl: thresholds(tl, var, 3) for tl in ("C", "E")}
    print("\n" + "=" * 118)
    print(f"E. 「条件付け」の増分と近傍（条件量 {var}）")
    print("=" * 118)
    for tl, uni, cond in (("E", {0: (0, 10), 1: (0, 10), 2: (0, 10)},
                           {0: (0, 14), 1: (0, 10), 2: (0, 6)}),):
        for w in W:
            u, _ = rule_recs(tl, w, var, THR[tl], uni)
            c, _ = rule_recs(tl, w, var, THR[tl], cond)
            ds, dr = boot(c, u)
            su, sc = agg(u, NDAYS[w]), agg(c, NDAYS[w])
            print(f"  型{tl} {WLAB[w][:2]}  一律(0,10) {su['shown']:.2f}% → 2次元 {sc['shown']:.2f}%"
                  f"   Δ {ds[0]:+.2f}[{ds[1]:+.2f},{ds[2]:+.2f}]"
                  f"   ΔROI {dr[0]:+.1f}[{dr[1]:+.1f},{dr[2]:+.1f}]")
    print("\n  -- 近傍（型E・2次元の三つ組をずらす）--")
    print(HEAD)
    NB = {
        "(0,14)/(0,10)/(0,6) 採用候補": {0: (0, 14), 1: (0, 10), 2: (0, 6)},
        "(0,12)/(0,10)/(0,8)": {0: (0, 12), 1: (0, 10), 2: (0, 8)},
        "(0,16)/(0,10)/(0,5)": {0: (0, 18), 1: (0, 10), 2: (0, 5)},
        "(0,14)/(0,12)/(0,6)": {0: (0, 14), 1: (0, 12), 2: (0, 6)},
        "(5,14)/(5,10)/(5,6)": {0: (5, 14), 1: (5, 10), 2: (5, 6)},
        "(10,14)/(5,10)/(0,6)": {0: (10, 14), 1: (5, 10), 2: (0, 6)},
        "(0,14)/(0,10)/(0,6) 2分位版": None,
    }
    for w in W:
        print(f"\n  [{WLAB[w]}]")
        base = [r["base"] for r in pop("E", w)]
        print(line("現行", agg(base, NDAYS[w])))
        for name, mp in NB.items():
            if mp is None:
                continue
            rec, f = rule_recs("E", w, var, THR["E"], mp)
            ds, _ = boot(rec, base)
            print(line(name, agg(rec, NDAYS[w]))
                  + f"  Δ{ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]")
    # 条件量を替える
    print("\n  -- 条件量を替える（同じ三つ組 (0,14)/(0,10)/(0,6)）--")
    for v2 in ("sig14", "sig12", "sp5", "sp14", "q_m3", "hs_trio", "axis"):
        t2 = thresholds("E", v2, 3)
        # hs_trio は向きが逆（安い＝低配当想定）なので群を反転
        mp = ({0: (0, 6), 1: (0, 10), 2: (0, 14)} if v2 == "hs_trio"
              else {0: (0, 14), 1: (0, 10), 2: (0, 6)})
        out = []
        for w in W:
            rec, _ = apply_rule(pop("E", w), v2, t2, mp)
            base = [r["base"] for r in pop("E", w)]
            ds, _ = boot(rec, base)
            ctrl = perm_control("E", w, v2, t2, mp)
            wins = sum(1 for cc in ctrl if agg(rec, NDAYS[w])["shown"] > cc["shown"])
            out.append(f"{agg(rec, NDAYS[w])['shown']:5.2f}% ({ds[0]:+5.2f}) 対照{wins:2d}/20")
        print(f"    {v2:9s}  確認 {out[0]}   探索 {out[1]}")


def tau_adapt(r, L=0.0, kmax=None):
    """帯 L で**入稿ゲートを通る最大点数**を選ぶ（`F_line` と同じ考え方＝条件量を持たない）。"""
    ks = [k for k in GRID[r["type"]][1] if kmax is None or k <= kmax]
    best = None
    for k in ks:
        a = arm(r, L, k)
        if a and a["gate"]:
            best = a
    return best


def section_f(var="sig14"):
    THR = {tl: thresholds(tl, var, 3) for tl in ("C", "E")}
    SP50 = {tl: float(np.percentile([r["sp5"] for r in pop(tl, "explore")], 50))
            for tl in ("C", "E")}
    print("\n" + "=" * 118)
    print("F. 条件量を持たない対抗腕（τ適応）と §5 の腕 — 型E 単体とラインナップ")
    print("=" * 118)

    def build(w, kind, tl="E"):
        out = []
        for r in pop(tl, w):
            if kind == "base":
                out.append(r["base"]); continue
            if kind == "cond2d":
                lk = {0: (0, 14), 1: (0, 10), 2: (0, 6)}[grp_of(r, var, THR[tl])]
                a = arm(r, *lk)
            elif kind == "tau0":
                a = tau_adapt(r, 0.0)
            elif kind == "tau15":
                a = tau_adapt(r, 15.0)
            elif kind == "s5":
                a = arm(r, 0.0, 5) if r["sp5"] >= SP50[tl] else None
            out.append(a if (a and a["gate"]) else r["base"])
        return out

    for w in W:
        print(f"\n  [型E 単体・{WLAB[w]}]")
        print(HEAD)
        base = build(w, "base")
        print(line("現行 L30 k14", agg(base, NDAYS[w])))
        for kind, nm in (("cond2d", "2次元 sig14→(0,14)/(0,10)/(0,6)"),
                         ("tau0", "τ適応 L0・ゲートを通る最大点数"),
                         ("tau15", "τ適応 L15・同上"),
                         ("s5", "§5 Σp5上位半分→(0,5)")):
            rec = build(w, kind)
            ds, dr = boot(rec, base)
            print(line(nm, agg(rec, NDAYS[w]))
                  + f"  Δ{ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]"
                  f"  ΔROI{dr[0]:+5.1f}[{dr[1]:+5.1f},{dr[2]:+5.1f}]")
        # 2次元 vs τ適応 の直接比較
        c2, t0 = build(w, "cond2d"), build(w, "tau0")
        ds, _ = boot(c2, t0)
        print(f"    → 2次元 − τ適応(L0) = {ds[0]:+.2f}[{ds[1]:+.2f},{ds[2]:+.2f}]pt")

    print("\n  [ラインナップ全体（型E だけ差し替え）]")
    for w in W:
        print(f"\n  {WLAB[w]}")
        print(HEAD)
        def lineup(kind):
            rec = []
            for r in BYW[w]:
                if not r["axis_ok"] or not r["base"]["gate"]:
                    continue
                if r["type"] != "E" or kind == "base":
                    rec.append(r["base"]); continue
                if kind == "cond2d":
                    lk = {0: (0, 14), 1: (0, 10), 2: (0, 6)}[grp_of(r, var, THR["E"])]
                    a = arm(r, *lk)
                elif kind == "tau0":
                    a = tau_adapt(r, 0.0)
                elif kind == "s5":
                    a = arm(r, 0.0, 5) if r["sp5"] >= SP50["E"] else None
                rec.append(a if (a and a["gate"]) else r["base"])
            return rec
        b = lineup("base")
        print(line("現行", agg(b, NDAYS[w])))
        for kind, nm in (("cond2d", "E を 2次元 (0,14)/(0,10)/(0,6)"),
                         ("tau0", "E を τ適応(L0)"),
                         ("s5", "E に §5 の腕")):
            rec = lineup(kind)
            ds, dr = boot(rec, b)
            print(line(nm, agg(rec, NDAYS[w]))
                  + f"  Δ{ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]"
                  f"  ΔROI{dr[0]:+5.1f}[{dr[1]:+5.1f},{dr[2]:+5.1f}]")


def tau_adapt_T(r, L, T):
    """帯 L で「平均想定払戻 > T」を保つ最大点数（`F_line` / `TRIO_LINE_SIGMA_MAX` と同型）。"""
    best = None
    for k in GRID[r["type"]][1]:
        a = arm(r, L, k)
        if a and a["gate"] and a["mean"] > T:
            best = a
    return best


def section_g(var="sig14"):
    """τ適応の目標払戻 T を掃く＝「帯と点数を同時に逆向きへ動かす」の1本のダイヤル。"""
    THR = {tl: thresholds(tl, var, 3) for tl in ("C", "E")}
    print("\n" + "=" * 118)
    print("G. τ適応の目標払戻 T を掃く（帯は外し、点数はゲートが決める）")
    print("=" * 118)
    for tl in ("C", "E"):
        for w in W:
            print(f"\n  [型{tl}・{WLAB[w]}]")
            print(HEAD)
            base = [r["base"] for r in pop(tl, w)]
            print(line(f"現行 L{PROD[tl][0]:.0f} k{PROD[tl][1]}", agg(base, NDAYS[w])))
            for L in (0.0, 5.0, 15.0):
                for T in (20_000, 25_000, 30_000, 35_000, 45_000):
                    rec = []
                    for r in pop(tl, w):
                        a = tau_adapt_T(r, L, T)
                        rec.append(a if a else r["base"])
                    ds, dr = boot(rec, base)
                    print(line(f"τ適応 L={L:.0f} T={T//1000}千円", agg(rec, NDAYS[w]))
                          + f"  Δ{ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]"
                          f"  ΔROI{dr[0]:+5.1f}[{dr[1]:+5.1f},{dr[2]:+5.1f}]")
    # τ適応が選ぶ点数と条件量の対応（＝ユーザーの言う逆向きの同時操作になっているか）
    print("\n  -- τ適応(L0,T=2万) が選ぶ点数と条件量 sig14 の群 --")
    for tl in ("C", "E"):
        for w in W:
            rs = pop(tl, w)
            ks = defaultdict(list)
            for r in rs:
                a = tau_adapt_T(r, 0.0, 20_000)
                if a:
                    ks[grp_of(r, var, THR[tl])].append(a["k"])
            msg = " / ".join(f"群{g}(n={len(v)}) 平均{np.mean(v):.1f}点"
                             for g, v in sorted(ks.items()))
            print(f"    型{tl} {WLAB[w][:2]}  {msg}")


def section_h(var="sig14"):
    """価格（平均想定払戻中央）を横軸にした効率フロンティア。

    🔴 `type_c.md` の作法（**平均想定払戻を揃えて比べる**）をそのまま2次元へ広げたもの。
       「帯と点数を同時に逆向きへ動かす」が本当に効くなら、τ適応の点列は
       固定k の点列より**同じ価格で上**に来るはずである。
    """
    THR = {tl: thresholds(tl, var, 3) for tl in ("C", "E")}
    COND = {"C": {0: (15, 14), 1: (15, 16), 2: (10, 10)},
            "E": {0: (0, 14), 1: (0, 10), 2: (0, 6)}}
    print("\n" + "=" * 118)
    print("H. 価格をそろえて比べる（横軸＝平均想定払戻の中央値）")
    print("=" * 118)
    for tl in ("C", "E"):
        Lp = PROD[tl][0]
        for w in W:
            rs = pop(tl, w)
            base = [r["base"] for r in rs]
            print(f"\n  [型{tl}・{WLAB[w]}]  n={len(rs):,}")
            print(f"  {'腕':30s} {'点数':>5s} {'平均想定払戻中央':>14s} {'表示的中%':>9s} "
                  f"{'払戻中央':>9s} {'10万+/日':>8s} {'ROI%':>6s}")
            def show(nm, rec):
                s = agg(rec, NDAYS[w])
                print(f"  {nm:30s} {s['k']:5.1f} {s['med_mean']:14,.0f} {s['shown']:9.2f} "
                      f"{s['med']:9,.0f} {s['big']:8.3f} {s['roi']:6.1f}")
            show("現行", base)
            for fam, L in ((f"固定k 帯{Lp:.0f}", Lp), ("固定k 帯なし", 0.0)):
                for k in GRID[tl][1]:
                    rec = [(arm(r, L, k) if (arm(r, L, k) or {}).get("gate") else r["base"])
                           for r in rs]
                    show(f"{fam} k={k}", rec)
            for L in (0.0, Lp):
                for T in (20, 22, 25, 28, 30, 33, 35, 40):
                    rec = []
                    for r in rs:
                        a = tau_adapt_T(r, L, T * 1000)
                        rec.append(a if a else r["base"])
                    show(f"τ適応 帯{L:.0f} T={T}千", rec)
            rec, _ = rule_recs(tl, w, var, THR[tl], COND[tl])
            show("2次元 条件付き(sig14)", rec)



def section_i(var="sig14"):
    """型C の τ適応 の中身を割る＝「帯下1点を守るために点数を減らす」だけなのか。"""
    print("\n" + "=" * 118)
    print("I. 型C の τ適応 の分解（帯は15のまま・点数だけがレースごとに動く）")
    print("=" * 118)
    for w in W:
        rs = pop("C", w)
        base = [r["base"] for r in rs]
        print(f"\n  [{WLAB[w]}]  n={len(rs):,}")
        print(HEAD)
        print(line("現行（k=12・落ちたら差込を外す）", agg(base, NDAYS[w])))
        for kmax, nm in ((12, "差込を保ち k を12から減らす"),
                         (16, "同上＋通るなら k を16まで増やす"),
                         (None, "同上（格子の上限まで）")):
            rec = []
            for r in rs:
                a = tau_adapt(r, 15.0, kmax)
                rec.append(a if a else r["base"])
            ds, dr = boot(rec, base)
            print(line(nm, agg(rec, NDAYS[w]))
                  + f"  Δ{ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]"
                  f"  ΔROI{dr[0]:+5.1f}[{dr[1]:+5.1f},{dr[2]:+5.1f}]")
        # 帯下差込を最初から持たない台での τ適応（差込の寄与を外す）
    print("\n  -- ラインナップ全体（型C だけ τ適応 帯15 T=2万・型E は現行）--")
    for w in W:
        rec_b, rec_a = [], []
        for r in BYW[w]:
            if not r["axis_ok"] or not r["base"]["gate"]:
                continue
            rec_b.append(r["base"])
            if r["type"] == "C":
                a = tau_adapt(r, 15.0, None)
                rec_a.append(a if a else r["base"])
            else:
                rec_a.append(r["base"])
        ds, dr = boot(rec_a, rec_b)
        print(f"\n  [{WLAB[w]}]")
        print(HEAD)
        print(line("現行", agg(rec_b, NDAYS[w])))
        print(line("型C を τ適応", agg(rec_a, NDAYS[w]))
              + f"  Δ{ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]"
              f"  ΔROI{dr[0]:+5.1f}[{dr[1]:+5.1f},{dr[2]:+5.1f}]")
    print("\n  -- 型C+型E 同時（C:τ適応帯15 / E:τ適応帯0 T=3.3万 = 価格を保つ点）--")
    for w in W:
        rec_b, rec_a = [], []
        for r in BYW[w]:
            if not r["axis_ok"] or not r["base"]["gate"]:
                continue
            rec_b.append(r["base"])
            a = None
            if r["type"] == "C":
                a = tau_adapt(r, 15.0, None)
            elif r["type"] == "E":
                a = tau_adapt_T(r, 0.0, 33_000)
            rec_a.append(a if a else r["base"])
        ds, dr = boot(rec_a, rec_b)
        print(f"\n  [{WLAB[w]}]")
        print(HEAD)
        print(line("現行", agg(rec_b, NDAYS[w])))
        print(line("C=τ適応 / E=τ適応T33千", agg(rec_a, NDAYS[w]))
              + f"  Δ{ds[0]:+5.2f}[{ds[1]:+5.2f},{ds[2]:+5.2f}]"
              f"  ΔROI{dr[0]:+5.1f}[{dr[1]:+5.1f},{dr[2]:+5.1f}]")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "A"
    v = sys.argv[2] if len(sys.argv) > 2 else "sig14"
    if "A" in what:
        section_a()
    if "B" in what:
        section_b(v, 3)
    if "C" in what:
        section_c(v)
    if "D" in what:
        section_d(v)
    if "E" in what:
        section_e(v)
    if "F" in what:
        section_f(v)
    if "G" in what:
        section_g(v)
    if "H" in what:
        section_h(v)
    if "I" in what:
        section_i(v)