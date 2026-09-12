#!/usr/bin/env python3
"""05-3 as-of 検証（2026-07-17〜09-10・7車・DB のみ・入稿時点に実在した板だけを使う）。

  入稿の波: morning 07:20 / noon 13:00 / night 18:00（cron 実測）
  使える板: morning→ 'morning'(snapshot_at<07:20 の日のみ＝08-08 以降) / noon→'h12' / night→'h14'（'h18' は境界）
  現行運用は**全レースを 07:20 に組む**ので、その場合は全波が 'morning' 板。
"""
import os, sys, pickle, itertools, json, datetime as dt
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score
from scipy.stats import spearmanr
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from mkfeat import CANON3, C3IDX, CAR_IN
JST = dt.timezone(dt.timedelta(hours=9)); rng = np.random.default_rng(0)
d = pickle.load(open(os.path.join(D, "asof.pkl"), "rb"))
races, ent, snap, tf, fin, picks = d["races"], d["ent"], d["snap"], d["tf"], d["fin"], d["picks"]
races = races.set_index("race_key")
races["first_h"] = [dt.datetime.fromtimestamp(s, JST).hour for s in races.first_start]
races["wave"] = np.where(races.first_h >= 18, "night", np.where(races.first_h >= 12, "noon", "morning"))
# ── モデル側（wt_entries の vintage 予測）──
ent = ent.dropna(subset=["p3"]).copy(); ent["p3"] = ent.p3.astype(float) / 100; ent["pw"] = ent.pw.astype(float) / 100
P3, PW, MARK, FIN = {}, {}, {}, {}
for k, g in ent.groupby("race_key"):
    if len(g) != 7: continue
    g = g.sort_values("frame_no")
    P3[k] = g.p3.values; PW[k] = g.pw.values; MARK[k] = g.mark.values; FIN[k] = g.fin.values
keys = [k for k in races.index if k in P3 and isinstance(races.at[k, "win_combo"], str)]
print(f"対象 {len(keys)}R（p3 と結果あり）")
# ── 板 ──
def vec(g):
    v = np.full(35, np.nan)
    for c, o in zip(g.comb, g.odds):
        try: o = float(o)
        except (TypeError, ValueError): continue
        if 0 < o < 9999:
            j = C3IDX.get(frozenset(int(x) for x in str(c).replace("=", "-").split("-")))
            if j is not None: v[j] = o
    return v
SN = {}   # (race, stype) -> (vec, at)
for (k, st), g in snap.groupby(["race_key", "stype"]):
    SN[(k, st)] = (vec(g), g["at"].iloc[0])
FIN_ODDS = {k: vec(g) for k, g in fin.groupby("race_key")}
TFF = {(k, st): int(n) for k, st, n in tf.itertuples(index=False)}

def feats(k, odds):
    p3 = P3[k]; order = np.argsort(-p3); a1, a2 = int(order[0]), int(order[1])
    f = np.isfinite(odds); nf = int(f.sum())
    out = dict(n_fill=nf, axis_sum=float(p3[a1] + p3[a2]))
    pw = PW[k]; v = pw[pw > 0]; v = v / v.sum(); out["pw_ent"] = float(-(v * np.log(v)).sum())
    mk = MARK[k]; out["agree_mark"] = float({a1, a2} == {i for i in range(7) if mk[i] in (1, 2)})
    if nf < 1:
        out.update(mk_axis=np.nan, mk_top2=np.nan, agree2=np.nan, mk_ent=np.nan, resid_axis=np.nan, log_fav=np.nan,
                   mk_rank_a1=np.nan, mk_rank_a2=np.nan, mk_pair=np.nan); return out
    inv = np.where(f, 1.0 / np.where(f, odds, 1), 0); q = inv / inv.sum(); m = CAR_IN @ q; mo = np.argsort(-m)
    out.update(mk_axis=float(m[a1] + m[a2]), mk_top2=float(m[mo[0]] + m[mo[1]]),
               agree2=float({a1, a2} == {int(mo[0]), int(mo[1])}),
               mk_ent=float(-(q[q > 0] * np.log(q[q > 0])).sum() / np.log(35)),
               resid_axis=float(p3[a1] + p3[a2] - m[a1] - m[a2]), log_fav=float(np.log(odds[f].min())),
               mk_rank_a1=float(np.flatnonzero(mo == a1)[0] + 1), mk_rank_a2=float(np.flatnonzero(mo == a2)[0] + 1),
               mk_pair=float(q[CAR_IN[a1] & CAR_IN[a2]].sum()))
    return out

def outcome(k):
    p3 = P3[k]; order = np.argsort(-p3) + 1
    top3 = {int(x) for x in races.at[k, "win_combo"].split("-")}
    pay = races.at[k, "tf_pay"]
    return dict(sorou=float(order[0] in top3 and order[1] in top3), tf_pay=float(pay) if pd.notna(pay) else np.nan,
                big=float(pd.notna(pay) and pay >= 100), a1_out=float(order[0] not in top3))

WAVE_SNAP = {"morning": "morning", "noon": "h12", "night": "h14"}
rows = []
for k in keys:
    w = races.at[k, "wave"]
    base = dict(race_key=k, date=races.at[k, "race_date"], wave=w, rtype=races.at[k, "race_type"], **outcome(k))
    for scheme, st in (("current", "morning"), ("deferred", WAVE_SNAP[w]), ("h18", "h18"), ("final", "final")):
        if st == "final":
            odds = FIN_ODDS.get(k); at = None
        else:
            v = SN.get((k, st)); odds, at = (v if v else (None, None))
        if odds is None:
            continue
        if st == "morning" and (at is None or at.strftime("%H:%M:%S") >= "07:20:00"):
            continue   # 入稿後に撮られた朝の板は使えない
        r = dict(base); r.update(scheme=scheme, stype=st, tf_fill=TFF.get((k, st), -1) if st != "final" else 210, **feats(k, odds))
        rows.append(r)
df = pd.DataFrame(rows)
df.to_pickle(os.path.join(D, "asof_feats.pkl"))

# ═══ 1. 可用性 ═══
print("\n== 1. 可用性: 入稿時点の三連複の板の充足（7車・2026-07-17〜09-10。morning 板は 08-08 以降の 07:0x 取得分のみ）==")
def avail(sub, label):
    n = len(sub)
    if n == 0: print(f"  {label:34s} n=0"); return
    f = sub.n_fill.values; t = sub.tf_fill.values.astype(float); t[t < 0] = 0
    print(f"  {label:34s} n={n:5d}  三連複 平均{f.mean()/35*100:5.1f}%  全35点 {np.mean(f==35)*100:5.1f}%  半分未満 {np.mean(f<18)*100:5.1f}%  0点 {np.mean(f==0)*100:5.1f}%"
          f"   三連単 平均{t.mean()/210*100:5.1f}%  半分未満 {np.mean(t<105)*100:5.1f}%")
all_keys = pd.DataFrame([dict(race_key=k, wave=races.at[k, "wave"], date=races.at[k, "race_date"]) for k in keys])
print("  [現行運用: 全レースを 07:20 に組む → 'morning' 板(07:0x)]  ※板が無い日の分は分母から外れる")
for w in ("morning", "noon", "night"):
    sub = df[(df.scheme == "current") & (df.wave == w)]
    n_all = ((all_keys.wave == w) & (all_keys.date >= "2026-08-08")).sum()
    avail(sub, f"波={w}（08-08以降 {n_all}R 中 板あり {len(sub)}）")
print("  [波を遅らせる: noon→h12(12:00) / night→h14(14:00) / night→h18(18:00・境界)]")
for w, sch in (("noon", "deferred"), ("night", "deferred"), ("night", "h18")):
    sub = df[(df.scheme == sch) & (df.wave == w)]
    n_all = (all_keys.wave == w).sum()
    avail(sub, f"波={w} {sub.stype.iloc[0] if len(sub) else ''}（{n_all}R 中 板あり {len(sub)}）")
print("  [参考: h12 の板が無いレースの割合（cron が撮れなかった日）]")
for w in ("noon", "night"):
    sub = all_keys[all_keys.wave == w]; have = sub.race_key.map(lambda k: (k, "h12") in SN).mean()
    print(f"    波={w} h12 あり {have*100:.1f}%")

# ═══ 2. 記述（充足で層別）═══
def desc(sub, label):
    print(f"\n  -- {label} n={len(sub)} --")
    for k in ("mk_axis", "resid_axis", "mk_ent", "log_fav"):
        v = sub[k].values; m = np.isfinite(v)
        if m.sum() < 60: print(f"    {k:10s} n不足"); continue
        qs = np.quantile(v[m], [1/3, 2/3]); b = np.digitize(v, qs)
        s = " | ".join(f"T{j+1}: そろい{sub.sorou[m&(b==j)].mean()*100:5.1f}% 払戻中央{np.nanmedian(sub.tf_pay[m&(b==j)])*100:8,.0f} 100倍+{sub.big[m&(b==j)].mean()*100:4.1f}% (n={int((m&(b==j)).sum())})" for j in range(3))
        print(f"    {k:10s} {s}")
    for v in (0.0, 1.0):
        mm = sub.agree2 == v
        if mm.sum(): print(f"    agree2={int(v):d} n={mm.sum():4d} そろい {sub.sorou[mm].mean()*100:5.1f}% 払戻中央 {np.nanmedian(sub.tf_pay[mm])*100:8,.0f} 100倍+ {sub.big[mm].mean()*100:4.1f}%")
    for v in (0.0, 1.0):
        mm = sub.agree_mark == v
        if mm.sum(): print(f"    印一致={int(v):d} n={mm.sum():4d} そろい {sub.sorou[mm].mean()*100:5.1f}%   （参考: 既にモデルに入っている WT印）")
print("\n== 2. 記述: 入稿時点の板の量 → 決着（充足で層別）==")
cur = df[df.scheme == "current"]
desc(cur[cur.n_fill == 35], "現行(07:20)・板が全35点埋まっている")
desc(cur[(cur.n_fill >= 18) & (cur.n_fill < 35)], "現行(07:20)・18〜34点")
desc(cur[cur.n_fill < 18], "現行(07:20)・18点未満（薄い）")
dfr = df[df.scheme == "deferred"]
desc(dfr[(dfr.wave != "morning") & (dfr.n_fill == 35)], "遅らせた波(noon h12/night h14)・全35点")
desc(dfr[(dfr.wave != "morning") & (dfr.n_fill < 35)], "遅らせた波・35点未満")
desc(df[df.scheme == "final"], "【上限・look-ahead】確定オッズ")

# ═══ 3. 増分 ═══
def oof(X, y, dates, folds=5, seed=0):
    ud = np.array(sorted(set(dates))); r = np.random.default_rng(seed); r.shuffle(ud)
    fo = np.array([{d: j % folds for j, d in enumerate(ud)}[d] for d in dates]); p = np.zeros(len(y))
    for f in range(folds):
        tr, te = fo != f, fo == f
        m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)); m.fit(X[tr], y[tr]); p[te] = m.predict_proba(X[te])[:, 1]
    return p
def boot(y, p0, p1, B=600):
    out = []; n = len(y)
    for _ in range(B):
        s = rng.integers(0, n, n)
        if y[s].min() == y[s].max(): continue
        out.append(roc_auc_score(y[s], p1[s]) - roc_auc_score(y[s], p0[s]))
    return np.percentile(out, [2.5, 97.5])
print("\n== 3. 増分 AUC（目的=軸2そろい・base = axis_sum, pw_ent, 印一致・ロジスティック OOF）==")
MK = ["mk_axis", "agree2", "mk_ent", "resid_axis", "log_fav", "mk_rank_a2"]
for label, sub in (("現行07:20・全35点", cur[cur.n_fill == 35]), ("現行07:20・全部(充足も特徴に)", cur),
                   ("遅らせた波 noon/night・全35点", dfr[(dfr.wave != "morning") & (dfr.n_fill == 35)]),
                   ("【上限】確定オッズ", df[df.scheme == "final"])):
    sub = sub.dropna(subset=MK)
    if len(sub) < 200: print(f"  {label}: n={len(sub)} 不足"); continue
    y = sub.sorou.values; dates = sub.date.values
    Xb = sub[["axis_sum", "pw_ent", "agree_mark"]].values.astype(float)
    Xm = sub[MK].values.astype(float)
    if "充足" in label: Xm = np.column_stack([Xm, sub.n_fill.values])
    p0 = oof(Xb, y, dates); p1 = oof(np.column_stack([Xb, Xm]), y, dates); p2 = oof(Xm, y, dates)
    a0, a1 = roc_auc_score(y, p0), roc_auc_score(y, p1); lo, hi = boot(y, p0, p1)
    # 2つの半窓で符号
    half = dates < sorted(set(dates))[len(set(dates))//2]; sg = []
    for hm in (half, ~half):
        if len(set(y[hm])) == 2: sg.append(roc_auc_score(y[hm], p1[hm]) - roc_auc_score(y[hm], p0[hm]))
    print(f"  {label:30s} n={len(sub):5d} base {a0:.4f} → +市場 {a1:.4f} Δ{a1-a0:+.4f} [{lo:+.4f},{hi:+.4f}]  市場だけ {roc_auc_score(y, p2):.4f}  半窓Δ {' / '.join(f'{s:+.4f}' for s in sg)}")
    for k in MK:
        pk = oof(np.column_stack([Xb, sub[[k]].values.astype(float)]), y, dates); ak = roc_auc_score(y, pk); lo, hi = boot(y, p0, pk)
        print(f"      +{k:11s} Δ{ak-a0:+.4f} [{lo:+.4f},{hi:+.4f}]   Spearman vs axis_sum {spearmanr(sub[k], sub.axis_sum)[0]:+.3f} / vs 印一致 {spearmanr(sub[k], sub.agree_mark)[0]:+.3f}")
