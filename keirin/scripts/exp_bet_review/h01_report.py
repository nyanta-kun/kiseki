#!/usr/bin/env python3
"""H01 Step1 集計: h01_step1_recs.pkl から対比較表（markdown）を出す。"""
from __future__ import annotations
import pickle, statistics, sys
from collections import defaultdict
import numpy as np
from h01_common import D
import src.type_lab as TL

NB, SEED = 2000, 20261005
d = pickle.load(open(D / "h01_step1_recs.pkl", "rb"))
ND = d["ndays"]
cur, lb = d["cur"]["recs"], d["lb"]["recs"]
ALLD = sorted({r["day"] for r in cur} | {r["day"] for r in lb})
DI = {x: i for i, x in enumerate(ALLD)}
H1D = np.array([x < "2025-07-01" for x in ALLD])


def agg(recs, sel=lambda r: True):
    """日ごとの (n, inv, pay, shown_hit, b10, hit>0)。"""
    a = np.zeros((len(ALLD), 6))
    for r in recs:
        if not sel(r):
            continue
        j = DI[r["day"]]
        a[j, 0] += 1; a[j, 1] += r["inv"]; a[j, 2] += r["pay"]
        a[j, 3] += r["pay"] > r["inv"]; a[j, 4] += r["pay"] >= 100_000; a[j, 5] += r["pay"] > 0
    return a


def metrics(a, mask=None):
    if mask is not None:
        a = a[mask]
    n, inv, pay, sh, b10 = a[:, 0].sum(), a[:, 1].sum(), a[:, 2].sum(), a[:, 3].sum(), a[:, 4].sum()
    nd = len(a)
    return dict(n=n, roi=pay / inv * 100 if inv else np.nan, shown=sh / n * 100 if n else np.nan,
                b10=b10 / nd if nd else np.nan, perday=n / nd if nd else np.nan, hits=int(a[:, 5].sum()),
                hitdays=int((a[:, 5] > 0).sum()), inv=inv, pay=pay)


def boot(f, n_days, seed=SEED):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(NB):
        s = rng.integers(0, n_days, n_days)
        out.append(f(s))
    return np.nanquantile(out, [0.025, 0.975])


def delta_row(label, sel=lambda r: True, mask=None, denom_days=None):
    ac, al = agg(cur, sel), agg(lb, sel)
    mk = np.ones(len(ALLD), bool) if mask is None else mask
    idx = np.flatnonzero(mk)
    ac, al = ac[idx], al[idx]
    mc, ml = metrics(ac), metrics(al)
    def dd(key):
        def f(s):
            x, y = metrics(ac[s]), metrics(al[s])
            return y[key] - x[key]
        return f
    res = {}
    for key in ("roi", "shown", "b10", "perday"):
        lo, hi = boot(dd(key), len(idx))
        res[key] = (ml[key] - mc[key], lo, hi)
    return mc, ml, res


def fmt_row(label, mc, ml, res):
    return (f"| {label} | {mc['n']:.0f}→{ml['n']:.0f} | {mc['roi']:.2f} | {ml['roi']:.2f} | "
            f"{res['roi'][0]:+.2f} [{res['roi'][1]:+.2f}, {res['roi'][2]:+.2f}] | "
            f"{mc['shown']:.2f}→{ml['shown']:.2f} ({res['shown'][0]:+.2f} [{res['shown'][1]:+.2f}, {res['shown'][2]:+.2f}]) | "
            f"{mc['b10']:.3f}→{ml['b10']:.3f} ({res['b10'][0]:+.3f} [{res['b10'][1]:+.3f}, {res['b10'][2]:+.3f}]) |")


HDR = ("| 区分 | 件数 cur→lb | ROI cur | ROI lb | ΔROI [95%CI] | 表示的中% cur→lb (Δ [CI]) | 10万+/日 cur→lb (Δ [CI]) |\n"
       "|---|---|---|---|---|---|---|")
out = []
P = out.append

# ── 全体 ──
mc, ml, res = delta_row("全体")
P("### 全体（2025・7車・365開催日）\n"); P(HDR); P(fmt_row("全体", mc, ml, res))
tot = (mc, ml, res)
P("\n| 腕 | 件/日 | 的中件数(払戻>0) | 的中のあった日 | 表示的中% | ROI% | 10万+/日 | 払戻中央(表示的中) |\n|---|---|---|---|---|---|---|---|")
for nm, recs in (("cur", cur), ("lb", lb)):
    m = metrics(agg(recs))
    hits = sorted(r["pay"] for r in recs if r["pay"] > r["inv"])
    P(f"| {nm} | {m['perday']:.2f} | {m['hits']} | {m['hitdays']} | {m['shown']:.2f} | {m['roi']:.2f} | {m['b10']:.3f} | {statistics.median(hits):,.0f} |")

# ── 半期 ──
P("\n### 上期 / 下期\n"); P(HDR)
half = {}
for lab, mk in (("上期(1〜6月)", H1D), ("下期(7〜12月)", ~H1D)):
    mc_, ml_, rs_ = delta_row(lab, mask=mk); half[lab] = rs_["roi"][0]
    P(fmt_row(lab, mc_, ml_, rs_))

# ── 商品別 ──
P("\n### 商品別（main 枠 / highpay 枠は plan キー別）\n"); P(HDR)
plans = sorted({r["plan"] for r in cur} | {r["plan"] for r in lb}, key=lambda k: -sum(1 for r in cur if r["plan"] == k))
for pk in plans:
    mc_, ml_, rs_ = delta_row(pk, sel=lambda r, pk=pk: r["plan"] == pk)
    P(fmt_row(pk, mc_, ml_, rs_))
P("\n(LB 適用対象 = A_hit / B_hit / C_hit / E_hit / F_hit のみ。他は元の P のままなので Δ は母集団の連動分だけ)")

# ── 上位除外 ──
P("\n### 上位 k 件の的中を除いた回収率（該当レコードの投資・払戻とも除外）\n\n| 腕 | 除外なし | 上位1除外 | 上位3除外 | 上位5除外 |\n|---|---|---|---|---|")
for nm, recs in (("cur", cur), ("lb", lb)):
    srt = sorted(recs, key=lambda r: -r["pay"])
    tinv, tpay = sum(r["inv"] for r in recs), sum(r["pay"] for r in recs)
    vals = []
    for k in (0, 1, 3, 5):
        vals.append((tpay - sum(r["pay"] for r in srt[:k])) / (tinv - sum(r["inv"] for r in srt[:k])) * 100)
    P(f"| {nm} | " + " | ".join(f"{v:.2f}" for v in vals) + " |")

# ── 買い目が変わったレース ──
bk = lambda recs: {(r["race_key"], r["slot"]): r for r in recs}
bc, bl = bk(cur), bk(lb)
both = set(bc) & set(bl); only_c = set(bc) - set(bl); only_l = set(bl) - set(bc)
chg_any = {k for k in both if bc[k]["stakes"] != bl[k]["stakes"]}
chg = {k for k in both if set(bc[k]["stakes"]) != set(bl[k]["stakes"])}      # 買う目の集合が違う
chg_st = chg_any - chg                                                       # 目は同じで賭け金だけ違う
lb_ev = d["lb_events"]
app = {k for k in both if k[0] in lb_ev and bc[k]["plan"] in TL.PLANS and bc[k]["plan"] in ("A_hit", "B_hit", "C_hit", "E_hit", "F_hit")}
chg_app = chg & app
P("\n### 買い目が変わったレース\n")
P(f"- cur 売り {len(bc)} / lb 売り {len(bl)} / 両方 {len(both)} / cur のみ {len(only_c)} / lb のみ {len(only_l)}")
P(f"- 両方で売ったうち**買う目の集合が変わった**: **{len(chg)} / {len(both)} = {len(chg)/len(both)*100:.2f}%**"
  f"（商品別 { {k: sum(1 for x in chg if bc[x]['plan']==k) for k in ('A_hit','B_hit','C_hit','E_hit','F_hit')} }）")
P(f"- 目は同じで賭け金（100円単位の按分）だけ変わった: {len(chg_st)}R（E_hit/F_hit の conf 配分のみ。合計 {len(chg_any)} = {len(chg_any)/len(both)*100:.2f}%）")
P(f"- LB が掛かりうるレース（k1/k3 のラインがある ∧ LB 対象プラン）: {len(app)}、うち目の集合が変わった {len(chg_app)} ({len(chg_app)/max(len(app),1)*100:.1f}%)")
P(f"- 入稿ゲート等で売りが入れ替わったレース: cur のみ {len(only_c)} / lb のみ {len(only_l)}")
# 変わったレースだけの成績
def sub(keys, bd):
    inv = sum(bd[k]["inv"] for k in keys); pay = sum(bd[k]["pay"] for k in keys)
    return inv, pay
ic, pc = sub(chg, bc); il, pl = sub(chg, bl)
ica, pca = sub(chg_any, bc); ila, pla = sub(chg_any, bl)
P(f"- 賭け金だけの変化を含む全変化 {len(chg_any)}R の ROI: cur {pca/ica*100:.2f}% → lb {pla/ila*100:.2f}%")
P(f"- 目の集合が変わったレースだけの ROI: cur {pc/ic*100:.2f}% → lb {pl/il*100:.2f}%（{len(chg)}R。投資 cur {ic:,.0f} / lb {il:,.0f}）、"
  f"表示的中 cur {sum(bc[k]['pay']>bc[k]['inv'] for k in chg)} → lb {sum(bl[k]['pay']>bl[k]['inv'] for k in chg)} 件")
if True:
    rng = np.random.default_rng(SEED)
    ddays = sorted({bc[k]["day"] for k in chg})
    byd = defaultdict(lambda: np.zeros(4))
    for k in chg:
        byd[bc[k]["day"]] += [bc[k]["inv"], bc[k]["pay"], bl[k]["inv"], bl[k]["pay"]]
    arr = np.array([byd[x] for x in ddays])
    bs = []
    for _ in range(NB):
        s = rng.integers(0, len(arr), len(arr)); t = arr[s].sum(0)
        bs.append(t[3] / t[2] * 100 - t[1] / t[0] * 100)
    lo, hi = np.quantile(bs, [0.025, 0.975])
    P(f"- 目の集合が変わったレースだけの ΔROI = {pl/il*100 - pc/ic*100:+.2f}pt [{lo:+.2f}, {hi:+.2f}]（{len(ddays)}開催日で日ブートストラップ）")
# cheap flips / line swap
flips = 0; fl_pairs = []
for k in both:
    pk = bc[k]["plan"]
    thr = TL.CHEAP_SHARE_MIN.get(pk)
    if thr is None:
        continue
    cs, cl = d["cheap"][k[0]]
    if (cs >= thr) != (cl >= thr):
        flips += 1
P(f"- cheap_target（A_hit/B_hit の目標 5万→2.5万）が反転したレース: {flips}")
P(f"- line_swap 発火（build 呼び出し単位・売らなかった候補を含む）: cur {d['cur']['cnt']['ls_fire']}/{d['cur']['cnt']['ls_calls']} → lb {d['lb']['cnt']['ls_fire']}/{d['lb']['cnt']['ls_calls']}")

# ── 基準判定 ──
r = tot[2]
c1 = r["roi"][1] > 0
c2 = half["上期(1〜6月)"] > 0 and half["下期(7〜12月)"] > 0
c3 = r["shown"][0] >= -1.0
c4 = r["b10"][0] >= 0
P("\n### 事前登録の基準（機械判定）\n\n| 基準 | 値 | 判定 |\n|---|---|---|")
P(f"| ΔROI の CI 下限 > 0（2025 通算） | {r['roi'][0]:+.2f} [{r['roi'][1]:+.2f}, {r['roi'][2]:+.2f}] | {'満たす' if c1 else '満たさない'} |")
P(f"| 上期・下期とも点推定 > 0 | 上期 {half['上期(1〜6月)']:+.2f} / 下期 {half['下期(7〜12月)']:+.2f} | {'満たす' if c2 else '満たさない'} |")
P(f"| 表示的中の低下 ≤ 1.0pt | Δ {r['shown'][0]:+.2f}pt | {'満たす' if c3 else '満たさない'} |")
P(f"| 10万+/日が減らない | Δ {r['b10'][0]:+.4f} | {'満たす' if c4 else '満たさない'} |")
mcw, mlw = tot[0], tot[1]
P(f"| サンプル量: 的中30件以上 ∧ 開催日60日以上 | 的中 cur {mcw['hits']} / lb {mlw['hits']}、開催日 {ND}（変わったレースの的中 {sum(bl[k]['pay']>0 for k in chg)}件・{len({bl[k]['day'] for k in chg if bl[k]['pay']>0})}日） | {'満たす' if mlw['hits']>=30 and ND>=60 else '満たさない'}（全体）|")
print("\n".join(out))
open(D / "h01_report_tables.md", "w").write("\n".join(out))
