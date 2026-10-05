#!/usr/bin/env python3
"""H16 集計: Step0（2025 の比の分布・2026 朝/最終の持続性）と 腕の比較表。出力は markdown を stdout へ。"""
from __future__ import annotations
import itertools, pickle, statistics, sys
from collections import defaultdict
import numpy as np
from h01_common import D, PERMS
import lineup_sim as S

NB, SEED = 2000, 20261005
C3 = S.C3
HIT3 = ("A_hit", "B_hit", "C_hit", "E_hit", "F_hit")
d = pickle.load(open(D / "h16_recs.pkl", "rb"))
ND = d["ndays"]
ARMS = ["cur", "a2", "a3", "a3b", "a3o"]
NAME = {"cur": "① 現行(三連単)", "a2": "② 3組以下→三連複", "a3": "③ ②のうち予測三連複≥予測合成", "a3b": "③b(参考)賭け金保存",
        "a3o": "③o(参考・look-ahead)確定で有利な組のみ"}


def q(a, ps=(0, 5, 25, 50, 75, 95, 100)):
    a = np.asarray(a, float)
    return " / ".join(f"{np.percentile(a, p):.3f}" for p in ps)


# ------------------------------------------------------------------ Step0 (2025)
def step0():
    tf = dict(np.load(D / "h01_final_tf_2025.npz", allow_pickle=True))
    t3 = dict(np.load(D / "h16_final_trio_2025.npz", allow_pickle=True))
    assert (tf["KEY"] == t3["KEY"]).all()
    row = {str(k): i for i, k in enumerate(tf["KEY"])}
    PI = {p: i for i, p in enumerate(PERMS)}
    CI = {frozenset(c): i for i, c in enumerate(C3)}
    out = defaultdict(lambda: dict(rb=[], r6=[], ng=[]))
    seen = set()
    for r in d["cur"]:
        if r["plan"] not in HIT3 or r["trio"]:
            continue
        k = r["race_key"]
        if (k, r["plan"]) in seen or k not in row:
            continue
        seen.add((k, r["plan"]))
        i = row[k]
        fin, f3 = tf["FIN"][i], t3["FIN3"][i]
        groups = defaultdict(list)
        for p in r["stakes"]:
            groups[frozenset(p)].append(p)
        ng = len(groups)
        for g, ps in groups.items():
            o3 = f3[CI[g]]
            if not np.isfinite(o3):
                continue
            ob = [fin[PI[p]] for p in ps]
            o6 = [fin[PI[p]] for p in itertools.permutations(sorted(g))]
            if not all(np.isfinite(ob)):
                continue
            cb = 1.0 / sum(1.0 / v for v in ob)
            for key in (r["plan"], "ALL", "ng<=3" if ng <= 3 else "ng>=4"):
                out[key]["rb"].append(o3 / cb)
                out[key]["ng"].append(ng)
                if all(np.isfinite(o6)):
                    c6 = 1.0 / sum(1.0 / v for v in o6)
                    out[key]["r6"].append(o3 / c6)
    return out


def pr_step0(out):
    print("| 区分 | 組数 | 三連複÷買った目の合成: 平均 | 中央 | ≥1.0 の割合 | 分位(0/5/25/50/75/95/100) |")
    print("|---|---|---|---|---|---|")
    for key in ("ALL", "ng<=3", "ng>=4") + HIT3:
        a = np.array(out[key]["rb"])
        if len(a) == 0:
            continue
        print(f"| {key}（÷買った目） | {len(a):,} | {a.mean():.3f} | {np.median(a):.3f} | {(a >= 1).mean()*100:.1f}% | {q(a)} |")
        b = np.array(out[key]["r6"])
        if len(b):
            print(f"| {key}（÷6通りすべて） | {len(b):,} | {b.mean():.3f} | {np.median(b):.3f} | {(b >= 1).mean()*100:.1f}% | {q(b)} |")


# ------------------------------------------------------------------ 持続性 (2026)
def persistence():
    s = pickle.load(open(D / "h16_snap_2026.pkl", "rb"))
    odds = s["odds"]
    res = dict(bought=[], all35=[])
    for rk, pk, dt, cs, st in s["picks"]:
        o = odds[rk]
        groups = defaultdict(list)
        for c in cs:
            groups[frozenset(c)].append(c)
        for g, ps in groups.items():
            try:
                m3, f3 = o["m3"][g], o["f3"][g]
                mb = 1.0 / sum(1.0 / o["mtf"][p] for p in ps)
                fb = 1.0 / sum(1.0 / o["ftf"][p] for p in ps)
            except KeyError:
                continue
            res["bought"].append((rk, pk, dt, len(groups), m3 / mb, f3 / fb))
    for rk in {p[0] for p in s["picks"]}:
        o = odds[rk]
        if len(o["m3"]) < 35 or len(o["f3"]) < 35:
            continue
        mt = {p: v for p, v in o["mtf"].items()}
        ft = o["ftf"]
        if len(mt) < 210 or len(ft) < 210:
            continue
        for g in o["m3"]:
            if g not in o["f3"]:
                continue
            ps = list(itertools.permutations(sorted(g)))
            m6 = 1.0 / sum(1.0 / mt[p] for p in ps)
            f6 = 1.0 / sum(1.0 / ft[p] for p in ps)
            res["all35"].append((rk, "", "", 0, o["m3"][g] / m6, o["f3"][g] / f6))
    return res


def pr_persist(res):
    def corr(rows, sel=lambda r: True):
        rows = [r for r in rows if sel(r)]
        m = np.array([r[4] for r in rows]); f = np.array([r[5] for r in rows])
        n = len(rows)
        if n < 10:
            return n, None
        lm, lf = np.log(m), np.log(f)
        rho = float(np.corrcoef(lm, lf)[0, 1])
        # Spearman
        rm = np.argsort(np.argsort(m)); rf = np.argsort(np.argsort(f))
        sp = float(np.corrcoef(rm, rf)[0, 1])
        # 朝の比 >=1 だったとき最終も >=1 の割合 / 朝<1
        up = f[m >= 1]; dn = f[m < 1]
        b = np.polyfit(lm, lf, 1)
        # レース単位ブートストラップ
        keys = sorted({r[0] for r in rows}); kidx = defaultdict(list)
        for i, r in enumerate(rows):
            kidx[r[0]].append(i)
        rng = np.random.default_rng(SEED); bs = []
        for _ in range(500):
            ii = np.concatenate([kidx[keys[j]] for j in rng.integers(0, len(keys), len(keys))])
            bs.append(np.corrcoef(lm[ii], lf[ii])[0, 1])
        lo, hi = np.percentile(bs, [2.5, 97.5])
        return n, dict(rho=rho, ci=(lo, hi), sp=sp, slope=float(b[0]), mm=float(m.mean()), fm=float(f.mean()),
                       up_n=len(up), up_f=float((up >= 1).mean()) if len(up) else float("nan"),
                       dn_n=len(dn), dn_f=float((dn >= 1).mean()) if len(dn) else float("nan"),
                       upm=float(up.mean()) if len(up) else float("nan"), dnm=float(dn.mean()) if len(dn) else float("nan"),
                       fup=float(up.mean()) if len(up) else float("nan"))
    print("| 母集団 | n | log比の相関(朝 vs 最終) [95%CI] | Spearman | 回帰傾き | 朝の比平均 | 最終の比平均 | 朝≥1: n / 最終≥1の割合 / 最終比平均 | 朝<1: n / 最終≥1の割合 / 最終比平均 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for nm, rows, sel in (("買った目の組（÷買った目の合成）", res["bought"], lambda r: True),
                          ("　うち 3組以下の買い目", res["bought"], lambda r: r[3] <= 3),
                          ("　うち 4組以上", res["bought"], lambda r: r[3] >= 4),
                          ("全35組（÷6通りすべて）", res["all35"], lambda r: True)):
        n, o = corr(rows, sel)
        if o is None:
            print(f"| {nm} | {n} | - |"); continue
        print(f"| {nm} | {n:,} | {o['rho']:.3f} [{o['ci'][0]:.3f}, {o['ci'][1]:.3f}] | {o['sp']:.3f} | {o['slope']:.3f} | "
              f"{o['mm']:.3f} | {o['fm']:.3f} | {o['up_n']:,} / {o['up_f']*100:.1f}% / {o['upm']:.3f} | {o['dn_n']:,} / {o['dn_f']*100:.1f}% / {o['dnm']:.3f} |")


# ------------------------------------------------------------------ 腕の比較
ALLD = sorted({r["day"] for a in ARMS for r in d[a]})
DI = {x: i for i, x in enumerate(ALLD)}
H1D = np.array([x < "2025-07-01" for x in ALLD])


def agg(recs, sel=lambda r: True):
    a = np.zeros((len(ALLD), 7))
    for r in recs:
        if not sel(r):
            continue
        j = DI[r["day"]]
        a[j, 0] += 1; a[j, 1] += r["inv"]; a[j, 2] += r["pay"]
        a[j, 3] += r["pay"] > r["inv"]; a[j, 4] += r["pay"] >= 100_000; a[j, 5] += r["pay"] > 0
        a[j, 6] += (r["pay"] > 0) and (r["pay"] <= r["inv"])
    return a


def met(a):
    n, inv, pay = a[:, 0].sum(), a[:, 1].sum(), a[:, 2].sum()
    nd = len(a)
    return dict(n=n, roi=pay / inv * 100 if inv else np.nan, shown=a[:, 3].sum() / n * 100 if n else np.nan,
                hit=a[:, 5].sum() / n * 100 if n else np.nan, gami=a[:, 6].sum() / a[:, 5].sum() * 100 if a[:, 5].sum() else np.nan,
                b10=a[:, 4].sum() / nd if nd else np.nan, perday=n / nd if nd else np.nan, hits=int(a[:, 5].sum()),
                shown_n=int(a[:, 3].sum()), days=int((a[:, 0] > 0).sum()))


def tail_ex(recs, k):
    rs = sorted(recs, key=lambda r: -r["pay"])[k:]
    inv = sum(r["inv"] for r in rs); pay = sum(r["pay"] for r in rs)
    return pay / inv * 100


def med_hit(recs):
    p = [r["pay"] for r in recs if r["pay"] > r["inv"]]
    return statistics.median(p) if p else float("nan")


def boot_delta(aa, ab, key, mask=None, nb=NB):
    """arm(ab) − cur(aa) の key 指標差の CI（開催日ブートストラップ）。"""
    if mask is not None:
        idx = np.flatnonzero(mask); aa, ab = aa[idx], ab[idx]
    nd = len(aa)
    rng = np.random.default_rng(SEED)
    out = []
    for _ in range(nb):
        s = rng.integers(0, nd, nd)
        out.append(met(ab[s])[key] - met(aa[s])[key])
    pt = met(ab)[key] - met(aa)[key]
    return pt, np.nanpercentile(out, [2.5, 97.5])


def arms_tables():
    A = {a: agg(d[a]) for a in ARMS}
    print("### 全体（2025・7車・365開催日）\n")
    print("| 腕 | 件数 | 件/日 | 的中件数 | 回収率% | 表示的中% | 的中率% | ガミ率%(的中のうち払戻≦投資) | 10万+/日 | 的中時払戻の中央(表示的中) | 上位1除外 | 上位3除外 | 上位5除外 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for a in ARMS:
        m = met(A[a])
        print(f"| {NAME[a]} | {m['n']:.0f} | {m['perday']:.2f} | {m['hits']} | {m['roi']:.2f} | {m['shown']:.2f} | {m['hit']:.2f} | {m['gami']:.2f} | {m['b10']:.3f} | "
              f"{med_hit(d[a]):,.0f} | {tail_ex(d[a],1):.2f} | {tail_ex(d[a],3):.2f} | {tail_ex(d[a],5):.2f} |")
    print("\n### ΔROI 等（腕 − ①・開催日ブートストラップ 2,000回・日単位ペア）\n")
    print("| 腕 | 範囲 | ΔROI pt [95%CI] | Δ表示的中 pt [95%CI] | Δ10万+/日 [95%CI] | Δ件/日 | 基準判定 |")
    print("|---|---|---|---|---|---|---|")
    verdict = {}
    for a in ARMS[1:]:
        res = {}
        for lab, mk in (("全体", None), ("上期(1-6月)", H1D), ("下期(7-12月)", ~H1D)):
            r, rc = boot_delta(A["cur"], A[a], "roi", mk)
            s, sc = boot_delta(A["cur"], A[a], "shown", mk)
            b, bc = boot_delta(A["cur"], A[a], "b10", mk)
            pd, _ = boot_delta(A["cur"], A[a], "perday", mk)
            res[lab] = (r, rc, s, sc)
            print(f"| {NAME[a]} | {lab} | {r:+.2f} [{rc[0]:+.2f}, {rc[1]:+.2f}] | {s:+.2f} [{sc[0]:+.2f}, {sc[1]:+.2f}] | {b:+.4f} [{bc[0]:+.4f}, {bc[1]:+.4f}] | {pd:+.2f} | |")
        r, rc, s, sc = res["全体"]
        c1 = rc[0] > 0
        c2 = res["上期(1-6月)"][0] > 0 and res["下期(7-12月)"][0] > 0
        c3 = s >= -1.0
        verdict[a] = (c1, c2, c3)
    print("\n### 事前登録基準の機械判定\n")
    print("| 腕 | ΔROI の CI 下限>0 | 上期・下期とも点推定>0 | 表示的中の低下≦1.0pt | 全て満たす | 的中30件以上 | 開催日60日以上 |")
    print("|---|---|---|---|---|---|---|")
    for a in ARMS[1:]:
        c1, c2, c3 = verdict[a]
        m = met(A[a])
        print(f"| {NAME[a]} | {'○' if c1 else '×'} | {'○' if c2 else '×'} | {'○' if c3 else '×'} | {'○' if (c1 and c2 and c3) else '×'} | "
              f"{'○' if m['hits']>=30 else '×'}({m['hits']}) | {'○' if m['days']>=60 else '×'}({m['days']}) |")
    # 同一レース対
    print("\n### 同一レース対の分解（race_key×枠 で突き合わせ）\n")
    kc = {(r["race_key"], r["slot"]): r for r in d["cur"]}
    print("| 腕 | 両腕で売れた件数 | ①ROI(同一レース) | 腕ROI(同一レース) | Δ(同一レース)pt | ①は売る・腕は見送り(件) | その①ROI | 腕だけ売る(件) | その腕ROI |")
    print("|---|---|---|---|---|---|---|---|---|")
    for a in ARMS[1:]:
        ka = {(r["race_key"], r["slot"]): r for r in d[a]}
        both = [k for k in kc if k in ka]
        ci_, pi_ = sum(kc[k]["inv"] for k in both), sum(kc[k]["pay"] for k in both)
        ai_, ap_ = sum(ka[k]["inv"] for k in both), sum(ka[k]["pay"] for k in both)
        lost = [k for k in kc if k not in ka]; gain = [k for k in ka if k not in kc]
        li, lp = sum(kc[k]["inv"] for k in lost), sum(kc[k]["pay"] for k in lost)
        gi, gp = sum(ka[k]["inv"] for k in gain), sum(ka[k]["pay"] for k in gain)
        print(f"| {NAME[a]} | {len(both):,} | {pi_/ci_*100:.2f} | {ap_/ai_*100:.2f} | {ap_/ai_*100 - pi_/ci_*100:+.2f} | {len(lost):,} | "
              f"{(lp/li*100 if li else float('nan')):.2f} | {len(gain):,} | {(gp/gi*100 if gi else float('nan')):.2f} |")
    # 商品別
    print("\n### 商品別（plan_key。①と腕の差）\n")
    plans = sorted({r["plan"] for r in d["cur"]})
    print("| 商品 | ①件数 | ①ROI | ①表示的中% | ②件数 | ②ROI | ②表示的中% | ΔROI(②−①) [95%CI] | ③件数 | ③ROI | ΔROI(③−①) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for p in plans:
        sel = lambda r, p=p: r["plan"] == p
        ac, a2, a3 = agg(d["cur"], sel), agg(d["a2"], sel), agg(d["a3"], sel)
        mc, m2, m3 = met(ac), met(a2), met(a3)
        if mc["n"] == 0 and m2["n"] == 0:
            continue
        pt, c = boot_delta(ac, a2, "roi", nb=1000)
        print(f"| {p} | {mc['n']:.0f} | {mc['roi']:.2f} | {mc['shown']:.2f} | {m2['n']:.0f} | {m2['roi']:.2f} | {m2['shown']:.2f} | "
              f"{pt:+.2f} [{c[0]:+.2f}, {c[1]:+.2f}] | {m3['n']:.0f} | {m3['roi']:.2f} | {m3['roi']-mc['roi']:+.2f} |")
    # ②で実際に三連複へ変えた買い目（グループ数別）
    print("\n### ②で三連複に変わったレースだけ（変換した hit 商品のレース・同一レース対）\n")
    conv = {k for (arm, k, pk), v in d["info"].items() if arm == "a2" and v["conv"] > 0}
    ka = {(r["race_key"], r["slot"]): r for r in d["a2"]}
    for lab, ng in (("1組", 1), ("2組", 2), ("3組", 3)):
        ks = {k for (arm, k, pk), v in d["info"].items() if arm == "a2" and v["conv"] > 0 and v["ng"] == ng}
        both = [k for k in kc if k in ka and k[0] in ks]
        if not both:
            continue
        ci_, pi_ = sum(kc[k]["inv"] for k in both), sum(kc[k]["pay"] for k in both)
        ai_, ap_ = sum(ka[k]["inv"] for k in both), sum(ka[k]["pay"] for k in both)
        lost = [k for k in kc if k not in ka and k[0] in ks]
        print(f"- 買い目が{lab}: 両腕で売れた {len(both):,} 件: ①ROI {pi_/ci_*100:.2f} → ②ROI {ap_/ai_*100:.2f}"
              f"（ゲート落ちで見送り {len(lost):,} 件）")


if __name__ == "__main__":
    what = sys.argv[1]
    if what == "step0":
        pr_step0(step0())
    elif what == "persist":
        pr_persist(persistence())
    else:
        arms_tables()
