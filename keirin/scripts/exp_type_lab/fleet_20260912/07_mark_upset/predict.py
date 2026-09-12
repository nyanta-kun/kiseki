#!/usr/bin/env python3
"""B. y=「1着が WT◎○ 以外」の予測可能性（既存量の天井・レース単位 LGB の増分・二重計上）。

台: event_table.pkl（本ディレクトリ）＋ 01_axis_bust/table.pkl（FEATURE_COLS_WT 70列の
レース単位集約 G1〜G4・sibling が作ったもの）を `i` で結合。
vintage: 探索OOS = 学習 2024-07〜2025-06 → 予測 2025-07〜12 / 確認 = 学習 〜2025-12 → 予測 2026。
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
FLEET = HERE.parent
TARGET = sys.argv[1] if len(sys.argv) > 1 else "y"        # "y" | "y23"

E = pickle.load((HERE / "event_table.pkl").open("rb"))
S = pickle.load((FLEET / "01_axis_bust" / "table.pkl").open("rb"))
T0, feat_cols = S["T"], S["feat_cols"]
drop = [c for c in T0.columns if c in E.columns and c != "i"]
T = E.merge(T0.drop(columns=drop), on="i", how="inner")
T["y23"] = T["y"] & T["mk23"]
print(f"結合 {len(T):,}R（event {len(E):,} / sibling {len(T0):,}）  目的={TARGET} 率 {T[TARGET].mean()*100:.2f}%")
yv = T[TARGET].astype(int).values

# ── 印由来のレース単位量（板から） ──
z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
Z = {k: z[k] for k in ("P3", "PW", "A_race_point", "A_line_size", "A_line_pos",
                       "A_is_line_leader", "A_prediction_mark", "LG", "ST", "BEHIND")}
z.close()
extra = []
for i, hon, tai in zip(T["i"].values, T["hon"].values, T["tai"].values):
    lg = [str(v) for v in Z["LG"][i]]
    st = [str(v) for v in Z["ST"][i]]
    lp, ls, ld = Z["A_line_pos"][i], Z["A_line_size"][i], Z["A_is_line_leader"][i]
    rp, beh, mk = Z["A_race_point"][i], Z["BEHIND"][i], Z["A_prediction_mark"][i]
    pw = Z["PW"][i] / max(Z["PW"][i].sum(), 1e-9)
    p3 = Z["P3"][i]
    same = (lg[hon-1] == lg[tai-1]) and lg[hon-1] not in ("", "0", "None")
    others = [c for c in range(1, 8) if c not in (hon, tai)]
    d = dict(
        hon_lpos=float(lp[hon-1]), hon_lsize=float(ls[hon-1]), hon_leader=float(ld[hon-1]),
        tai_lpos=float(lp[tai-1]), tai_lsize=float(ls[tai-1]), tai_leader=float(ld[tai-1]),
        hontai_same_line=int(same), hon_beh=float(beh[hon-1]), tai_beh=float(beh[tai-1]),
        hon_oi=int(st[hon-1] == "追"), hon_nige=int(st[hon-1] == "逃"),
        tai_oi=int(st[tai-1] == "追"), tai_nige=int(st[tai-1] == "逃"),
        hon_rp=float(rp[hon-1]), tai_rp=float(rp[tai-1]),
        rp_hon_minus_max_other=float(rp[hon-1] - max(rp[c-1] for c in others)),
        pw_other_sum=float(sum(pw[c-1] for c in others)),
        p3_other_max=float(max(p3[c-1] for c in others)),
        n_marked=int((mk > 0).sum()),
        sankaku_pw=float(next((pw[c-1] for c in range(1, 8) if mk[c-1] == 3), 0.0)),
        best_other_leader=int(any(ld[c-1] == 1 for c in others if pw[c-1] == max(pw[c-1] for c in others))),
    )
    extra.append(d)
X_extra = pd.DataFrame(extra, index=T.index)
T = pd.concat([T, X_extra], axis=1)

# ── 既存量 ──
EXIST = ["pw_ent", "axis_sum", "arare", "gap", "pw_gap12", "rp_sd"]
MARK = ["p_y_model", "pw_hon", "pw_tai", "pw_max_other", "p3_hon", "p3_tai",
        "hon_is_a1", "honta_is_axis", "agree", "pw_rank_hon", "pw_rank_tai", "p3_rank_hon"]
for c in ("hon_is_a1", "honta_is_axis", "agree"):
    T[c] = T[c].astype(int)

def split(name):
    if name == "explore":
        tr = (T.date >= "2024-07-01") & (T.date <= "2025-06-30")
        te = (T.date >= "2025-07-01") & (T.date <= "2025-12-31")
    else:
        tr = T.date <= "2025-12-31"
        te = T.date >= "2026-01-01"
    return tr.values, te.values

def auc(y, s):
    return roc_auc_score(y, s)

def boot_diff(y, s1, s0, n=400, seed=0):
    rng = np.random.default_rng(seed)
    N = len(y); ds = []
    for _ in range(n):
        b = rng.integers(0, N, N)
        try:
            ds.append(auc(y[b], s1[b]) - auc(y[b], s0[b]))
        except ValueError:
            pass
    return np.percentile(ds, [2.5, 97.5])

print("\n[B1] 単一量 AUC（符号は y と正相関になる向きへ揃える）")
print(f"  {'量':22s} {'探索OOS(2025H2)':>16s} {'確認(2026)':>12s}")
res_single = {}
for c in EXIST + MARK:
    line = []
    for w in ("explore", "confirm"):
        _, te = split(w)
        s = T.loc[te, c].astype(float).values
        a = auc(yv[te], s)
        if a < 0.5: a = 1 - a
        line.append(a)
    res_single[c] = line
    print(f"  {c:22s} {line[0]:16.4f} {line[1]:12.4f}")

def logit_fit(cols, w):
    tr, te = split(w)
    sc = StandardScaler().fit(T.loc[tr, cols].astype(float))
    m = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(T.loc[tr, cols].astype(float)), yv[tr])
    return m.predict_proba(sc.transform(T.loc[te, cols].astype(float)))[:, 1]

print("\n[B1b] 多変量ロジスティック（学習→予測は vintage）")
SETS = {
    "既存6量": EXIST,
    "1-pw◎-pw○ 単独": ["p_y_model"],
    "既存6量 + 1-pw◎-pw○": EXIST + ["p_y_model"],
    "既存6量 + 印由来12量": EXIST + MARK,
    "既存6量 + 印由来 + 印配置(ライン/脚質/得点)": EXIST + MARK + list(X_extra.columns),
}
preds_logit = {}
for nm, cols in SETS.items():
    line = []
    for w in ("explore", "confirm"):
        _, te = split(w)
        p = logit_fit(cols, w)
        preds_logit[(nm, w)] = p
        line.append(auc(yv[te], p))
    print(f"  {nm:40s} {line[0]:.4f} / {line[1]:.4f}")

# ── LGB レース単位モデル ──
G3 = [c for c in T.columns if c[:2] in ("m_", "s_", "x_", "n_") or c[:3] in ("a1_", "a2_")]
LEAK = {"a1", "a2", "a1_in3", "a2_in3", "y", "y23", "trio_pay", "tf_pay", "mk23", "both23"}
G3 = [c for c in G3 if c not in LEAK]
assert not any(c in LEAK for c in G3)
G12 = [c for c in ["p3_a1", "p3_a2", "pw_a1", "pw_a2", "p3_prod", "p3_min2", "p3_gap12", "p3_gap23",
                   "p3_sum3", "p3_ent", "p3_total", "pw_sum2", "same_line", "adjacent", "max_lsize",
                   "rp_top_gap", "dayi", "n_lines", "n_solo"] if c in T.columns]
G12 += [f"p3s{k}" for k in range(7)] + [f"pws{k}" for k in range(7)]
FULL = EXIST + MARK + list(X_extra.columns) + G12 + G3
FULL = list(dict.fromkeys(FULL))
print(f"\n[B2] LGB レース単位モデル  特徴 {len(FULL)} 列（うち 70列集約 {len(G3)}）")

def lgb_fit(cols, w, seeds=(0, 1, 2, 3, 4)):
    tr, te = split(w)
    Xtr, ytr = T.loc[tr, cols].astype(float), yv[tr]
    dates = T.loc[tr, "date"].values
    cut = np.quantile(dates.astype("datetime64[D]").astype(int), 0.8)
    es = dates.astype("datetime64[D]").astype(int) > cut
    ps = []
    for sd in seeds:
        m = lgb.LGBMClassifier(n_estimators=2000, learning_rate=0.03, num_leaves=15, reg_lambda=10.0,
                               subsample=0.8, subsample_freq=1, colsample_bytree=0.6,
                               min_child_samples=50, random_state=sd, verbose=-1)
        m.fit(Xtr[~es], ytr[~es], eval_set=[(Xtr[es], ytr[es])],
              callbacks=[lgb.early_stopping(100, verbose=False)])
        ps.append(m.predict_proba(T.loc[te, cols].astype(float))[:, 1])
    return np.mean(ps, axis=0), m

ARMS = {
    "LGB 既存6量+印由来のみ": EXIST + MARK,
    "LGB 確率・印配置・構造(70列集約なし)": EXIST + MARK + list(X_extra.columns) + G12,
    "LGB 全部": FULL,
}
BASE_COLS = ["p_y_model"]
out = {}
for nm, cols in ARMS.items():
    line = []
    for w in ("explore", "confirm"):
        tr, te = split(w)
        p, m = lgb_fit(cols, w)
        out[(nm, w)] = p
        a_single = auc(yv[te], p)
        # 増分: logit(p_y_model) + logit(p) のスタッキング（学習は te 内 2-fold ではなく、
        # 既存最良量に足す形の増分を te で直接評価する ＝ 係数は te で当てはめるので楽観側。
        # 楽観を避けるため係数は 1:1 の単純和（logit 空間）にする）
        base = np.log(np.clip(T.loc[te, "p_y_model"].values, 1e-6, 1 - 1e-6) /
                      (1 - np.clip(T.loc[te, "p_y_model"].values, 1e-6, 1 - 1e-6)))
        lp = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
        a_base = auc(yv[te], base)
        a_stack = auc(yv[te], base + lp)
        ci = boot_diff(yv[te], base + lp, base)
        ci2 = boot_diff(yv[te], lp, base)
        # 既存6量+p_y_model ロジスティックへの増分
        pl = preds_logit[("既存6量 + 1-pw◎-pw○", w)]
        lpl = np.log(np.clip(pl, 1e-6, 1 - 1e-6) / (1 - np.clip(pl, 1e-6, 1 - 1e-6)))
        ci3 = boot_diff(yv[te], lpl + lp, lpl)
        line.append((a_single, a_base, a_stack, ci, ci2, auc(yv[te], lpl + lp) - auc(yv[te], lpl), ci3))
    print(f"  {nm}")
    for w, (a1, a0, a2, ci, ci2, d3, ci3) in zip(("探索OOS", "確認"), line):
        print(f"    {w}: 単体 {a1:.4f}  基準(1-pw◎-pw○) {a0:.4f}  基準+LGB {a2:.4f}  "
              f"増分 {a2-a0:+.4f} [{ci[0]:+.4f},{ci[1]:+.4f}]  単体−基準 {a1-a0:+.4f} [{ci2[0]:+.4f},{ci2[1]:+.4f}]  "
              f"対(既存6量+基準ロジ) 増分 {d3:+.4f} [{ci3[0]:+.4f},{ci3[1]:+.4f}]")
    if nm == "LGB 全部":
        imp = pd.Series(m.booster_.feature_importance("gain"), index=cols).sort_values(ascending=False)
        print("    gain 上位15:", ", ".join(f"{k}:{v/imp.sum()*100:.1f}%" for k, v in imp.head(15).items()))

print("\n[B3] 二重計上の点検（確認窓・Spearman）")
_, te = split("confirm")
p_full = out[("LGB 全部", "confirm")]
for nm, s in (("pw_ent", T.loc[te, "pw_ent"].values), ("1-pw◎", 1 - T.loc[te, "pw_hon"].values),
              ("1-pw◎-pw○", T.loc[te, "p_y_model"].values), ("axis_sum(負)", -T.loc[te, "axis_sum"].values),
              ("pw_max_other", T.loc[te, "pw_max_other"].values)):
    r = spearmanr(p_full, s).correlation
    print(f"  LGB全部 vs {nm:14s} ρ={r:+.3f}")
print(f"  pw_ent vs 1-pw◎ ρ={spearmanr(T.loc[te,'pw_ent'], 1-T.loc[te,'pw_hon']).correlation:+.3f}   "
      f"pw_ent vs 1-pw◎-pw○ ρ={spearmanr(T.loc[te,'pw_ent'], T.loc[te,'p_y_model']).correlation:+.3f}")

print("\n[B4] 波乱度の分解: (a)◎○が弱い vs (b)別の車が強い")
for w in ("explore", "confirm"):
    _, te = split(w)
    a_a = auc(yv[te], 1 - T.loc[te, "pw_hon"].values - T.loc[te, "pw_tai"].values)
    a_a1 = auc(yv[te], 1 - T.loc[te, "pw_hon"].values)
    a_b = auc(yv[te], T.loc[te, "pw_max_other"].values)
    a_b2 = auc(yv[te], T.loc[te, "p3_other_max"].values)
    pab = logit_fit(["pw_hon", "pw_tai", "pw_max_other"], w)
    print(f"  {w}: (a) 1-pw◎ {a_a1:.4f} / 1-pw◎-pw○ {a_a:.4f}   (b) pw_max_other {a_b:.4f} / p3_other_max {a_b2:.4f}   (a)+(b) ロジ {auc(yv[te], pab):.4f}")

# 予測十分位の実測（確認窓・LGB全部）
print("\n[B5] LGB全部 の十分位 → 実測 y（確認窓）と払戻")
_, te = split("confirm")
D = T.loc[te].assign(p=p_full)
D["dq"] = pd.qcut(D.p, 10, labels=False)
g = D.groupby("dq").agg(pred=("p", "mean"), obs=(TARGET, "mean"), y23=("y23", "mean"), n=("p", "size"),
                        odds_med=("tf_odds", "median"))
print(g.round(3).to_string())
pickle.dump(dict(T=T[["i", "key", "date", "win", "type", "y", "y23", "p_y_model", "pw_ent"]].assign(
    p_lgb_confirm=np.nan), preds=out, preds_logit=preds_logit), (HERE / f"predict_{TARGET}.pkl").open("wb"))
