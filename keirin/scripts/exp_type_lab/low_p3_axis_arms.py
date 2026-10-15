#!/usr/bin/env python3
"""当てにいく商品で「p3[軸1] < q のとき軸1を外して組む」腕（本番関数で組む・日次上限/ゲート込み）。

腕:
  R1 drop  … 軸1を含む目を全部外す（三連複は order を1つ繰り下げ）
  R2 shift … 三連複のみ: 軸=#2,#3・軸1は相手の先頭に残す
  RND      … 同数のレースを無作為に R1 化（20seed）
比較は同一レース対（現行の売り行に対して差し替え）。
閾値は探索窓（2024-07〜2025-12）の売り行の p3[軸1] 分位。確認窓は 2026-01〜。
結果: docs/type_lab/low_p3_axis_shift_2026_10_15.md
出力: /tmp/low_p3_axis/arms.out（ctx キャッシュ cache.pkl も同所・初回約10分）
"""
import sys, pickle, random, statistics
from dataclasses import replace
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import numpy as np
import lineup_sim as S
import lineup_arms as LA
from src.type_lab import PLANS
HERE = Path("/tmp/low_p3_axis"); HERE.mkdir(exist_ok=True)
OUT = open(HERE / "arms.out", "w")
def p(*a):
    print(*a); print(*a, file=OUT); OUT.flush()

z = S.board()
m = (z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"]
idx = [int(i) for i in np.flatnonzero(m)]
cp = HERE / "cache.pkl"
if cp.exists():
    cache = pickle.load(open(cp, "rb"))
else:
    cache = {i: S.ctx(i) for i in idx}
    pickle.dump(cache, open(cp, "wb"))
ok = [i for i in idx if cache.get(i) is not None]
by_key = {cache[i].key: cache[i] for i in ok}
p(f"組めた {len(ok)}R")

def windows():
    e = [i for i in ok if "2024-07-01" <= cache[i].date <= "2025-12-31"]
    c = [i for i in ok if cache[i].date >= "2026-01-01"]
    return {"探索": e, "確認": c}
W = windows()
recs = {w: [r for r in LA.run("current", {}, W[w], cache) if r["slot"] == "main"] for w in W}
for w in W:
    p(f"{w}: 売り行 {len(recs[w])}  日数 {len({r['day'] for r in recs[w]})}")

P3 = z["P3"]
key2i = {cache[i].key: i for i in ok}
def p3_axis1(x):
    i = key2i[x.key]; return float(P3[i][x.shape.order[0]-1])

E_p3 = [p3_axis1(by_key[r["race_key"]]) for r in recs["探索"]]
QS = {q: float(np.quantile(E_p3, q/100)) for q in (10, 20, 30)}
p("閾値（探索窓の売り行の p3[軸1] 分位）:", {k: round(v, 3) for k, v in QS.items()})

def variant(x, mode):
    """mode: drop / shift。軸1を外した ctx を返す。"""
    o = x.shape.order
    a1 = o[0]
    y = S.Ctx(); y.__dict__.update(x.__dict__)
    if mode == "drop":
        y.shape = replace(x.shape, order=tuple(o[1:]))
        y.po_tf = {k: v for k, v in x.po_tf.items() if a1 not in k}
        y.pr_tf = {k: v for k, v in x.pr_tf.items() if a1 not in k}
        y.ord_tf = {k: v for k, v in x.ord_tf.items() if a1 not in k} if x.ord_tf else x.ord_tf
        y.po_t3 = {k: v for k, v in x.po_t3.items() if a1 not in k}
        y.pr_t3 = {k: v for k, v in x.pr_t3.items() if a1 not in k}
    else:  # shift: 三連複の軸だけ繰り下げ
        y.shape = replace(x.shape, order=tuple([o[1], o[2], o[0]] + list(o[3:])))
    return y

def rebuild(x, plan_key, mode):
    plan = PLANS[plan_key]
    if mode == "shift" and plan.bet_type != "trio":
        return None
    y = variant(x, mode)
    got = S.build(y, plan)
    if not got or not S.gate_ok(got[0], got[1], got[3]):
        return None
    st, odds, used, mean = got
    inv, pay = S.settle(x, st, used.bet_type == "trio")
    return inv, pay, len(st)

def stats(rows):
    inv = sum(r[0] for r in rows); pay = sum(r[1] for r in rows)
    shown = np.mean([r[1] > r[0] for r in rows]) * 100
    b4 = sum(1 for r in rows if r[1] >= 40000)
    return shown, pay / inv * 100, b4

def boot(base, alt, nb=2000, seed=7):
    rng = np.random.default_rng(seed)
    b = np.array(base); a = np.array(alt); n = len(b)
    ds, dr = [], []
    for _ in range(nb):
        k = rng.integers(0, n, n)
        ds.append(((a[k,1] > a[k,0]).mean() - (b[k,1] > b[k,0]).mean()) * 100)
        dr.append((a[k,1].sum()/a[k,0].sum() - b[k,1].sum()/b[k,0].sum()) * 100)
    return np.percentile(ds, [2.5, 97.5]), np.percentile(dr, [2.5, 97.5])

CACHE_ALT = {}
def alt_of(r, mode):
    k = (r["race_key"], mode)
    if k not in CACHE_ALT:
        CACHE_ALT[k] = rebuild(by_key[r["race_key"]], r["plan"], mode)
    return CACHE_ALT[k]

for w in W:
    ndays = len({r["day"] for r in recs[w]})
    p(f"\n=========== {w}窓")
    for q, th in QS.items():
        tgt = [r for r in recs[w] if p3_axis1(by_key[r["race_key"]]) < th]
        p(f"\n--- p3[軸1] < {th:.3f}（探索の下位{q}%）  対象 {len(tgt)}行 ({len(tgt)/len(recs[w])*100:.1f}%) {len(tgt)/ndays:.2f}件/日"
          f"  プラン内訳 " + ", ".join(f"{k}:{n}" for k, n in sorted(
              {r['plan']: sum(1 for s in tgt if s['plan']==r['plan']) for r in tgt}.items(), key=lambda t:-t[1])))
        base = [(r["inv"], r["pay"], r["n"]) for r in tgt]
        s0 = stats(base)
        p(f"  現行            表示的中 {s0[0]:6.2f}%  ROI {s0[1]:6.1f}  4万+ {s0[2]:4d}本 ({s0[2]/ndays:.3f}/日)")
        for mode in ("drop", "shift"):
            pairs = [(b, alt_of(r, mode)) for b, r in zip(base, tgt)]
            if mode == "shift":
                pairs = [(b, a) for (b, a), r in zip(pairs, tgt) if PLANS[r["plan"]].bet_type == "trio"]
                if not pairs: continue
            built = [(b, a) for b, a in pairs if a is not None]
            # 組めない・ゲート落ちは「現行のまま」とする（見送りにしない）
            alt = [a if a is not None else b for b, a in pairs]
            bb = [b for b, a in pairs]
            s1 = stats(alt); sb = stats(bb)
            ci_s, ci_r = boot(bb, alt)
            p(f"  {mode:<6} 組めた {len(built)}/{len(pairs)}  現行(同行) {sb[0]:6.2f}%/{sb[1]:6.1f}/{sb[2]}  → 案 {s1[0]:6.2f}%  ROI {s1[1]:6.1f}  4万+ {s1[2]:4d}"
              f"  Δ表示的中 {s1[0]-sb[0]:+.2f} [{ci_s[0]:+.2f},{ci_s[1]:+.2f}]  ΔROI {s1[1]-sb[1]:+.1f} [{ci_r[0]:+.1f},{ci_r[1]:+.1f}]")
            # プラン別
            for pk in sorted({r["plan"] for r in tgt}):
                sub = [(b, a if a is not None else b) for (b, a), r in zip(
                    [(b, alt_of(r, mode)) for b, r in zip(base, tgt)], tgt) if r["plan"] == pk]
                if mode == "shift" and PLANS[pk].bet_type != "trio": continue
                if len(sub) < 20: continue
                x0 = stats([s[0] for s in sub]); x1 = stats([s[1] for s in sub])
                p(f"       {pk:<7} n={len(sub):5d}  {x0[0]:6.2f}%/{x0[1]:6.1f} → {x1[0]:6.2f}%/{x1[1]:6.1f}  4万+ {x0[2]}→{x1[2]}")
        # 無作為対照: 同数を全売り行から無作為に drop
        if q == 20:
            ds, dr = [], []
            for seed in range(20):
                rs = random.Random(seed).sample(recs[w], len(tgt))
                b = [(r["inv"], r["pay"], r["n"]) for r in rs]
                a = [alt_of(r, "drop") or bb_ for r, bb_ in zip(rs, b)]
                sb, sa = stats(b), stats(a)
                ds.append(sa[0]-sb[0]); dr.append(sa[1]-sb[1])
            p(f"  無作為対照 drop（20seed）: Δ表示的中 中央 {statistics.median(ds):+.2f} [{min(ds):+.2f},{max(ds):+.2f}]  ΔROI 中央 {statistics.median(dr):+.1f} [{min(dr):+.1f},{max(dr):+.1f}]")
