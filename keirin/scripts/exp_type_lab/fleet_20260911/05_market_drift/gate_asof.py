#!/usr/bin/env python3
"""05-5 商品KPI: 入稿時点の板で「市場が自社の軸を支持していないレース」を落とす選別ゲート（第3層）を
本番の生成行（type_lab_picks paper/live・7車）で測る。無作為同数対照 20seed。日次上限は掛けない。"""
import os, sys, json, pickle, importlib.util
import numpy as np, pandas as pd
D = os.path.dirname(os.path.abspath(__file__)); REPO = "/Users/ysuzuki/GitHub/kiseki/keirin"
sys.path.insert(0, REPO)
from src.type_lab import sell_plans_for
_s = importlib.util.spec_from_file_location("tl_gate", "/Users/ysuzuki/GitHub/kiseki/backend/src/services/keirin_type_lab_gate.py")
GATE = importlib.util.module_from_spec(_s); _s.loader.exec_module(GATE)
rng = np.random.default_rng(7)
d = pickle.load(open(os.path.join(D, "asof.pkl"), "rb")); picks = d["picks"]; races = d["races"].set_index("race_key")
F = pd.read_pickle(os.path.join(D, "asof_feats.pkl"))
def min_pred(legs):
    try:
        v = [float(l["pred_odds"]) for l in (legs if isinstance(legs, list) else json.loads(legs)) if l.get("pred_odds")]
        return min(v) if v else None
    except Exception:
        return None
picks = picks.copy(); picks["min_pred"] = picks.legs.map(min_pred)
picks["pred_mean"] = picks.pred_mean.astype(float)
def gate_ok(r):
    if pd.notna(r.pred_mean) and r.pred_mean <= 20000: return False
    if r.min_pred is not None and r.min_pred < 2.0: return False
    return True
sold = []
for rk, g in picks.groupby("race_key"):
    g = g[g["mode"] == g["mode"].iloc[0]]
    rt = races.at[rk, "race_type"] if rk in races.index else None
    tl = g.tl.iloc[0]; pw = g.pw_ent.iloc[0]
    trio = g[g["plan"] == "A_trio"]; trio_ok = bool(len(trio)) and gate_ok(trio.iloc[0])
    keys = [p.key for p in sell_plans_for(tl, 7, rt, pw_ent=float(pw) if pd.notna(pw) else None, trio_ok=trio_ok)]
    if len(keys) != 1: continue
    r = g[g["plan"] == keys[0]]
    if not len(r): continue
    r = r.iloc[0]
    if pd.isna(r.hit): continue
    sold.append(dict(race_key=rk, date=str(races.at[rk, "race_date"]), plan=keys[0], axis=float(r.axis_sum), inv=float(r.budget),
                     pay=float(r.payout or 0), hit=bool(r.hit), ok_axis=GATE.passes_axis_gate(keys[0], float(r.axis_sum), 7), ok_gate=gate_ok(r),
                     n_legs=int(r.n_legs), mode=r["mode"]))
S = pd.DataFrame(sold); S = S[S.ok_axis & S.ok_gate]
print(f"売った行（sell_plans_for → 軸信頼ゲート → 入稿ゲート・上限なし）: {len(S)}R  {S.date.min()}〜{S.date.max()}  paper {int((S['mode']=='paper').sum())} / live {int((S['mode']=='live').sum())}")
print("  プラン:", S.plan.value_counts().to_dict())
def kpi(s):
    nd = len(set(s.date)); hits = s[s.pay > 0]; shown = hits[hits.pay > hits.inv]
    return dict(n=len(s), perday=len(s)/max(nd,1), shown=len(shown)/len(s)*100 if len(s) else 0, med=float(np.median(shown.pay)) if len(shown) else 0,
                big=(shown.pay >= 100000).sum()/max(nd,1), roi=s.pay.sum()/s.inv.sum()*100 if s.inv.sum() else 0)
def fmt(k): return f"n={k['n']:4d} {k['perday']:5.2f}件/日 表示的中 {k['shown']:5.2f}% 払戻中央 {k['med']:8,.0f} 10万+/日 {k['big']:.3f} ROI {k['roi']:5.1f}"
def boot_delta(full, kept, B=500):
    """表示的中の差（kept − full）のレース単位 bootstrap CI。"""
    fi = (full.pay > full.inv).values.astype(float); ki = np.isin(full.race_key.values, kept.race_key.values)
    out = []
    for _ in range(B):
        s = rng.integers(0, len(fi), len(fi)); a = fi[s]; k = ki[s]
        if k.sum() == 0: continue
        out.append((a[k].mean() - a.mean()) * 100)
    return np.percentile(out, [2.5, 97.5])
def run(label, scheme, cond_fn, windows):
    f = F[F.scheme == scheme].set_index("race_key")
    for wname, (lo, hi) in windows.items():
        s = S[(S.date >= lo) & (S.date <= hi)]
        s = s[s.race_key.isin(f.index)]
        if len(s) < 100: print(f"  {label} [{wname}] n={len(s)} 不足"); continue
        feat = f.loc[s.race_key]
        drop = cond_fn(feat).values
        kept = s[~drop]; k0, k1 = kpi(s), kpi(kept); lo_, hi_ = boot_delta(s, kept)
        wins = 0; ctrl = []
        for seed in range(20):
            r = np.random.default_rng(seed); idx = r.choice(len(s), len(kept), replace=False); c = kpi(s.iloc[idx]); ctrl.append(c["shown"])
            wins += k1["shown"] > c["shown"]
        print(f"  {label} [{wname}] 落とす {drop.mean()*100:4.1f}%")
        print(f"      全部   {fmt(k0)}")
        print(f"      ゲート {fmt(k1)}  Δ表示的中 {k1['shown']-k0['shown']:+.2f} [{lo_:+.2f},{hi_:+.2f}]  対照20seed に {wins}/20 勝（対照中央 {np.median(ctrl):.2f}%）")
W_cur = {"前半 08-08〜08-24": ("2026-08-08", "2026-08-24"), "後半 08-25〜09-10": ("2026-08-25", "2026-09-10")}
W_def = {"前半 07-17〜08-13": ("2026-07-17", "2026-08-13"), "後半 08-14〜09-10": ("2026-08-14", "2026-09-10")}
print("\n== A. 現行運用（07:20 の板）==")
run("G1 板18点以上 ∧ 市場の上位2車≠自社の軸2車 を落とす", "current", lambda f: (f.n_fill >= 18) & (f.agree2 == 0), W_cur)
run("G2 板18点以上 ∧ 市場の軸支持 mk_axis 下位1/3 を落とす", "current", lambda f: (f.n_fill >= 18) & (f.mk_axis < f.mk_axis[f.n_fill >= 18].quantile(1/3)), W_cur)
run("G3 板18点以上 ∧ 自社が市場より強気(resid_axis 上位1/3) を落とす", "current", lambda f: (f.n_fill >= 18) & (f.resid_axis > f.resid_axis[f.n_fill >= 18].quantile(2/3)), W_cur)
run("G4 板が全35点 ∧ 市場の上位2車≠軸2車 を落とす", "current", lambda f: (f.n_fill == 35) & (f.agree2 == 0), W_cur)
print("\n== B. 波を遅らせた場合（noon→h12 / night→h14 の板・morning 波はそのまま 07:20 板）==")
run("G1' 板35点 ∧ 市場の上位2車≠軸2車 を落とす", "deferred", lambda f: (f.n_fill == 35) & (f.agree2 == 0), W_def)
run("G2' 板35点 ∧ mk_axis 下位1/3 を落とす", "deferred", lambda f: (f.n_fill == 35) & (f.mk_axis < f.mk_axis[f.n_fill == 35].quantile(1/3)), W_def)
run("G5' 板18点以上 ∧ 市場の上位2車≠軸2車 を落とす", "deferred", lambda f: (f.n_fill >= 18) & (f.agree2 == 0), W_def)
print("\n== C. 【上限・look-ahead】確定オッズで同じゲートを掛けたら ==")
run("G1f 確定: 市場の上位2車≠軸2車 を落とす", "final", lambda f: f.agree2 == 0, W_def)
run("G2f 確定: mk_axis 下位1/3 を落とす", "final", lambda f: f.mk_axis < f.mk_axis.quantile(1/3), W_def)
