#!/usr/bin/env python3
"""05-4 市場の情報は「発走の何時間前」から現れるか（as-of 台・全スナップショット h10/h12/h14/h18/h20/morning）。
同じレースが複数のバケットに入るが、バケット内では1レース1行（最も遅いスナップを採る）。
比較: base(axis_sum, pw_ent, 印一致) vs +市場（板が全35点のレースだけ）。さらに同じレース集合で確定オッズの上限を並べる。"""
import os, sys, pickle, datetime as dt
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from mkfeat import C3IDX, CAR_IN
JST = dt.timezone(dt.timedelta(hours=9)); rng = np.random.default_rng(1)
d = pickle.load(open(os.path.join(D, "asof.pkl"), "rb"))
races, ent, snap, fin = d["races"].set_index("race_key"), d["ent"], d["snap"], d["fin"]
ent = ent.dropna(subset=["p3"]).copy(); ent["p3"] = ent.p3.astype(float) / 100; ent["pw"] = ent.pw.astype(float) / 100
P3, PW, MARK = {}, {}, {}
for k, g in ent.groupby("race_key"):
    if len(g) != 7: continue
    g = g.sort_values("frame_no"); P3[k] = g.p3.values; PW[k] = g.pw.values; MARK[k] = g.mark.values
def vec(g):
    v = np.full(35, np.nan)
    for c, o in zip(g.comb, g.odds):
        try: o = float(o)
        except (TypeError, ValueError): continue
        if 0 < o < 9999:
            j = C3IDX.get(frozenset(int(x) for x in str(c).replace("=", "-").split("-")))
            if j is not None: v[j] = o
    return v
def feats(k, odds):
    p3 = P3[k]; order = np.argsort(-p3); a1, a2 = int(order[0]), int(order[1])
    f = np.isfinite(odds); nf = int(f.sum())
    pw = PW[k]; v = pw[pw > 0]; v = v / v.sum(); mk = MARK[k]
    out = dict(n_fill=nf, axis_sum=float(p3[a1] + p3[a2]), pw_ent=float(-(v * np.log(v)).sum()),
               agree_mark=float({a1, a2} == {i for i in range(7) if mk[i] in (1, 2)}))
    if nf < 1: return None
    inv = np.where(f, 1.0 / np.where(f, odds, 1), 0); q = inv / inv.sum(); m = CAR_IN @ q; mo = np.argsort(-m)
    out.update(mk_axis=float(m[a1] + m[a2]), agree2=float({a1, a2} == {int(mo[0]), int(mo[1])}),
               mk_ent=float(-(q[q > 0] * np.log(q[q > 0])).sum() / np.log(35)),
               resid_axis=float(p3[a1] + p3[a2] - m[a1] - m[a2]), log_fav=float(np.log(odds[f].min())),
               mk_rank_a2=float(np.flatnonzero(mo == a2)[0] + 1))
    return out
def outcome(k):
    p3 = P3[k]; order = np.argsort(-p3) + 1; top3 = {int(x) for x in races.at[k, "win_combo"].split("-")}
    pay = races.at[k, "tf_pay"]
    return float(order[0] in top3 and order[1] in top3), float(pd.notna(pay) and pay >= 100)
rows = []
for (k, st), g in snap.groupby(["race_key", "stype"]):
    if k not in P3 or not isinstance(races.at[k, "win_combo"], str): continue
    at = g["at"].iloc[0].replace(tzinfo=JST); hrs = (races.at[k, "start_at"] - at.timestamp()) / 3600
    if hrs < 0: continue
    f = feats(k, vec(g))
    if f is None: continue
    s, b = outcome(k)
    rows.append(dict(race_key=k, date=races.at[k, "race_date"], stype=st, hrs=hrs, sorou=s, big=b, **f))
FINF = {}
for k, g in fin.groupby("race_key"):
    if k in P3 and isinstance(races.at[k, "win_combo"], str):
        f = feats(k, vec(g))
        if f: FINF[k] = f
df = pd.DataFrame(rows); print("rows", len(df), "races", df.race_key.nunique())
MK = ["mk_axis", "agree2", "mk_ent", "resid_axis", "log_fav", "mk_rank_a2"]
def oof(X, y, dates, folds=5, seed=0):
    ud = np.array(sorted(set(dates))); r = np.random.default_rng(seed); r.shuffle(ud)
    fo = np.array([{d: j % folds for j, d in enumerate(ud)}[d] for d in dates]); p = np.zeros(len(y))
    for f in range(folds):
        tr, te = fo != f, fo == f
        m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)); m.fit(X[tr], y[tr]); p[te] = m.predict_proba(X[te])[:, 1]
    return p
def boot(y, p0, p1, B=500):
    out = []; n = len(y)
    for _ in range(B):
        s = rng.integers(0, n, n)
        if y[s].min() == y[s].max(): continue
        out.append(roc_auc_score(y[s], p1[s]) - roc_auc_score(y[s], p0[s]))
    return np.percentile(out, [2.5, 97.5])
BUCK = [(0, 1.5), (1.5, 3), (3, 5), (5, 8), (8, 12), (12, 30)]
for target in ("sorou", "big"):
    print(f"\n== 目的={target}: 発走までの時間 × 板の充足 → 市場量の増分 ΔAUC（base=axis_sum,pw_ent,印一致）==")
    print("  発走まで      充足    n     base   +市場(as-of) Δ [95%CI]            同レースの確定オッズ Δ")
    for lo, hi in BUCK:
        sub = df[(df.hrs >= lo) & (df.hrs < hi)].sort_values("hrs").groupby("race_key").head(1)
        for fl, fm in (("全35点", sub.n_fill == 35), ("18〜34点", (sub.n_fill >= 18) & (sub.n_fill < 35)), ("<18点", sub.n_fill < 18)):
            s = sub[fm].dropna(subset=MK)
            if len(s) < 150 or len(set(s[target])) < 2: continue
            y = s[target].values; dates = s.date.values
            Xb = s[["axis_sum", "pw_ent", "agree_mark"]].values.astype(float)
            p0 = oof(Xb, y, dates); p1 = oof(np.column_stack([Xb, s[MK].values.astype(float)]), y, dates)
            Xf = np.array([[FINF[k][m] for m in MK] for k in s.race_key])
            p2 = oof(np.column_stack([Xb, Xf]), y, dates)
            a0, a1, a2 = roc_auc_score(y, p0), roc_auc_score(y, p1), roc_auc_score(y, p2)
            l1, h1 = boot(y, p0, p1); l2, h2 = boot(y, p0, p2)
            print(f"  {lo:4.1f}〜{hi:4.1f}h  {fl:7s} {len(s):5d}  {a0:.4f}  {a1:.4f} {a1-a0:+.4f} [{l1:+.4f},{h1:+.4f}]   {a2:.4f} {a2-a0:+.4f} [{l2:+.4f},{h2:+.4f}]")
