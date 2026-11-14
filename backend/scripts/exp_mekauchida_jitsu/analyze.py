"""メカウチダの確率（オッズ込み / 実）と kiseki の配信値の比較（探索）。

入力: horses.csv / picks.csv（履歴ページ）・kiseki.csv（hit_tier_picks = 発走10分前の配信値）
"""

import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import spearmanr

D = sys.argv[1]
EPS = 0.005
rng = np.random.default_rng(20261114)

h = pd.read_csv(f"{D}/horses.csv", dtype={"date": str})
k = pd.read_csv(f"{D}/kiseki.csv", dtype={"date": str})
k = k.rename(columns={"course_name": "venue", "race_number": "rn", "horse_number": "hn"})
m = h.merge(k, on=["date", "venue", "rn", "hn"], how="inner")
print(f"site rows {len(h)} / kiseki rows {len(k)} / matched {len(m)}")
site_races = h.groupby(["date", "venue", "rn"]).size()
both_races = m.groupby(["date", "venue", "rn"]).size()
print(f"races: site {len(site_races)} / matched {len(both_races)}")

# 確定したレースだけ。取消・除外（abnormality 1,2 等）は母集団から外す
m = m[m["finish_position"].notna() | m["abnormality_code"].fillna(0).gt(0)]
m = m[m["abnormality_code"].fillna(0).isin([0])]
m["key"] = m["date"] + "_" + m["venue"] + "_" + m["rn"].astype(str)
# 1 レースの全馬が揃い、勝ち馬がいるレースだけ
g = m.groupby("key")
ok = (
    g["finish_position"].apply(lambda s: (s == 1).sum() == 1)
    & g["pre_win_odds"].apply(lambda s: s.notna().all())
    & g["win_probability"].apply(lambda s: s.notna().all())
)
m = m[m["key"].isin(ok[ok].index)].copy()
print(f"analysable races {m['key'].nunique()} / horses {len(m)} / days {m['date'].nunique()}")

nfield = m.groupby("key")["hn"].transform("size")
m["slots"] = np.where(m["n_entries"] >= 8, 3, np.where(m["n_entries"] >= 5, 2, 0))
m = m[m["slots"] > 0]
m["y_win"] = (m["finish_position"] == 1).astype(int)
m["y_pl"] = (m["finish_position"] <= m["slots"]).astype(int)

# 市場: 発走10分前単勝 → 正規化
inv = 1 / m["pre_win_odds"]
m["mk_w"] = inv / inv.groupby(m["key"]).transform("sum")


def harville_place(df):
    out = np.zeros(len(df))
    p = df["mk_w"].to_numpy()
    slots = int(df["slots"].iloc[0])
    n = len(p)
    for i in range(n):
        tot = p[i]
        if slots >= 2:
            for j in range(n):
                if j == i:
                    continue
                pj_first = p[j]
                tot += pj_first * p[i] / (1 - pj_first)
                if slots >= 3:
                    for kk in range(n):
                        if kk in (i, j):
                            continue
                        tot += pj_first * p[kk] / (1 - pj_first) * p[i] / (1 - pj_first - p[kk])
        out[i] = tot
    return pd.Series(out, index=df.index)


m["mk_p"] = m.groupby("key", group_keys=False).apply(harville_place)
for c in ["p1", "r1", "p3", "r3"]:
    m[c] = m[c] / 100.0
# レース内正規化（整数%の丸め）
for c, tot in [("p1", 1), ("r1", 1)]:
    m[c + "n"] = m[c].clip(lower=EPS) / m[c].clip(lower=EPS).groupby(m["key"]).transform("sum")
m["kw"] = m["win_probability"].clip(lower=1e-4)
m["kw"] = m["kw"] / m["kw"].groupby(m["key"]).transform("sum")
m["kp"] = m["place_probability"]


def logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


# ---- 単体の成績 ----
def race_win_ll(col):
    return -np.log(m.loc[m["y_win"] == 1].set_index("key")[col].clip(lower=1e-6))


def race_pl_ll(col):
    p = np.clip(m[col], EPS, 1 - EPS)
    ll = -(m["y_pl"] * np.log(p) + (1 - m["y_pl"]) * np.log(1 - p))
    return ll.groupby(m["key"]).sum()


def boot(diff, n=4000):
    a = diff.to_numpy()
    idx = rng.integers(0, len(a), size=(n, len(a)))
    s = a[idx].mean(axis=1)
    return a.mean(), np.percentile(s, 2.5), np.percentile(s, 97.5)


print("\n== 単体: 勝ち馬の多項対数損失（レース平均・低いほど良い） ==")
W = {c: race_win_ll(c) for c in ["mk_w", "p1n", "r1n", "kw"]}
for c, lab in [
    ("mk_w", "市場(10分前)"),
    ("p1n", "サイト オッズ込み(2分前)"),
    ("r1n", "サイト 実"),
    ("kw", "kiseki 配信"),
]:
    print(f"  {lab:24s} {W[c].mean():.4f}")
print("\n== 単体: 複勝圏の対数損失（1頭あたり） ==")
P = {c: race_pl_ll(c) for c in ["mk_p", "p3", "r3", "kp"]}
nh = m.groupby("key").size()
for c, lab in [("mk_p", "市場 Harville"), ("p3", "サイト オッズ込み"), ("r3", "サイト 実"), ("kp", "kiseki 配信")]:
    print(f"  {lab:24s} {P[c].sum() / nh.sum():.4f}")

print("\n== 順位相関（レース内 Spearman の平均） ==")


def race_rho(a, b):
    v = []
    for _, d in m.groupby("key"):
        if len(d) >= 5:
            v.append(spearmanr(d[a], d[b]).correlation)
    return np.nanmean(v)


for a, b, lab in [
    ("r3", "kp", "実 × kiseki"),
    ("r3", "mk_p", "実 × 市場"),
    ("kp", "mk_p", "kiseki × 市場"),
    ("p3", "mk_p", "オッズ込み × 市場"),
    ("r1", "composite_index", "実1着 × kiseki 指数"),
]:
    print(f"  {lab:20s} {race_rho(a, b):.3f}")

# ---- 積み重ね（開催日を1日ずつ抜く） ----
days = sorted(m["date"].unique())


def fit_place(cols):
    X = np.column_stack([np.ones(len(m))] + [logit(m[c]) for c in cols])
    y = m["y_pl"].to_numpy()
    pred = np.zeros(len(m))
    for d in days:
        tr = (m["date"] != d).to_numpy()

        def nll(w):
            z = X[tr] @ w
            return np.sum(np.logaddexp(0, z) - y[tr] * z)

        w = minimize(nll, np.r_[0, np.ones(len(cols)) / len(cols)], method="BFGS").x
        pred[~tr] = 1 / (1 + np.exp(-(X[~tr] @ w)))
    p = np.clip(pred, 1e-6, 1 - 1e-6)
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    return pd.Series(ll, index=m.index).groupby(m["key"]).sum()


def fit_win(cols):
    X = np.column_stack([np.log(np.clip(m[c], EPS, None)) for c in cols])
    keys = m["key"].to_numpy()
    y = m["y_win"].to_numpy()
    codes, uniq = pd.factorize(keys)
    out = pd.Series(0.0, index=pd.Index(uniq))
    for d in days:
        tr = (m["date"] != d).to_numpy()

        def nll(w, mask):
            z = X[mask] @ w
            c = codes[mask]
            zmax = np.zeros(c.max() + 1)
            lse = np.zeros(c.max() + 1)
            np.maximum.at(zmax, c, z)  # z 以上
            np.add.at(lse, c, np.exp(z - zmax[c]))
            return -(np.sum(z * y[mask]) - np.sum((np.log(lse) + zmax)[np.unique(c)]))

        w = minimize(lambda w: nll(w, tr), np.ones(len(cols)) / len(cols), method="BFGS").x
        te = ~tr
        z = X[te] @ w
        df = pd.DataFrame({"k": keys[te], "z": z, "y": y[te]})
        df["lse"] = df.groupby("k")["z"].transform(lambda s: np.log(np.exp(s - s.max()).sum()) + s.max())
        ll = -(df.loc[df["y"] == 1].set_index("k")["z"] - df.loc[df["y"] == 1].set_index("k")["lse"])
        out.loc[ll.index] = ll
    return out


print("\n== 積み重ね（日を1つ抜いて係数を決める）・複勝圏 1頭あたり対数損失 ==")
S = {}
for name, cols in [
    ("kiseki", ["kp"]),
    ("kiseki+実", ["kp", "r3"]),
    ("実", ["r3"]),
    ("市場", ["mk_p"]),
    ("市場+実", ["mk_p", "r3"]),
    ("市場+kiseki", ["mk_p", "kp"]),
    ("市場+kiseki+実", ["mk_p", "kp", "r3"]),
    ("オッズ込み", ["p3"]),
    ("市場+kiseki+オッズ込み", ["mk_p", "kp", "p3"]),
]:
    S[name] = fit_place(cols)
    print(f"  {name:22s} {S[name].sum() / nh.sum():.4f}")
for a, b in [("kiseki+実", "kiseki"), ("市場+実", "市場"), ("市場+kiseki+実", "市場+kiseki"), ("市場+kiseki", "市場")]:
    mu, lo, hi = boot(S[a] - S[b])
    print(f"  {a} − {b}: レース平均 Δ={mu:+.4f} 95%CI [{lo:+.4f}, {hi:+.4f}]")

print("\n== 積み重ね・勝ち馬 多項対数損失（レース平均） ==")
T = {}
for name, cols in [
    ("kiseki", ["kw"]),
    ("kiseki+実", ["kw", "r1n"]),
    ("市場", ["mk_w"]),
    ("市場+実", ["mk_w", "r1n"]),
    ("市場+kiseki", ["mk_w", "kw"]),
    ("市場+kiseki+実", ["mk_w", "kw", "r1n"]),
]:
    T[name] = fit_win(cols)
    print(f"  {name:22s} {T[name].mean():.4f}")
for a, b in [("kiseki+実", "kiseki"), ("市場+実", "市場"), ("市場+kiseki+実", "市場+kiseki"), ("市場+kiseki", "市場")]:
    mu, lo, hi = boot(T[a] - T[b])
    print(f"  {a} − {b}: Δ={mu:+.4f} 95%CI [{lo:+.4f}, {hi:+.4f}]")

# ---- サイトの買い目 ----
pk = pd.read_csv(f"{D}/picks.csv", dtype={"date": str})
kk = k.copy()
pk = pk.merge(kk, on=["date", "venue", "rn", "hn"], how="left")
print(f"\n== サイトの買い目 {len(pk)} 点（kiseki と突き合わせ {pk['finish_position'].notna().sum()}） ==")
pk["win_ret"] = np.where(pk["finish_position"] == 1, pk["final_win_odds"], 0.0)
pk["pl_ret"] = np.where(pk["place_payout_odds"].notna() & (pk["place_payout_odds"] > 0), pk["place_payout_odds"], 0.0)
pk["mix"] = 0.3 * pk["win_ret"] + 0.7 * pk["pl_ret"]
print(
    pk[["date", "venue", "rn", "hn", "res", "finish_position", "final_win_odds", "place_payout_odds"]]
    .head(8)
    .to_string()
)
for col, lab in [("mix", "単3＋複7"), ("pl_ret", "複勝だけ"), ("win_ret", "単勝だけ")]:
    a = pk[col].to_numpy()
    s = a[rng.integers(0, len(a), size=(10000, len(a)))].mean(axis=1)
    print(
        f"  {lab}: 回収率 {a.mean():.3f}  95%CI [{np.percentile(s, 2.5):.3f}, {np.percentile(s, 97.5):.3f}]  P(<1)={np.mean(s < 1):.3f}"
    )
print(f"  複勝的中 {np.mean(pk['pl_ret'] > 0):.3f}")
# 買い目の kiseki 評価
pk["kp_rank"] = np.nan
km = k.copy()
km["kp_rank"] = km.groupby(["date", "venue", "rn"])["place_probability"].rank(ascending=False)
km["ci_rank"] = km.groupby(["date", "venue", "rn"])["composite_index"].rank(ascending=False)
pk = pk.drop(columns=["kp_rank"]).merge(
    km[["date", "venue", "rn", "hn", "kp_rank", "ci_rank"]], on=["date", "venue", "rn", "hn"], how="left"
)
print("  買い目の kiseki 複勝確率順位:", pk["kp_rank"].value_counts().sort_index().to_dict())
for lab, sel in [("kiseki 複勝5位内", pk["kp_rank"] <= 5), ("kiseki 複勝6位以下", pk["kp_rank"] > 5)]:
    a = pk.loc[sel, "mix"]
    print(f"  {lab}: n={sel.sum()} 単3＋複7 回収 {a.mean():.3f} / 複勝的中 {np.mean(pk.loc[sel, 'pl_ret'] > 0):.3f}")
m.to_csv(f"{D}/merged.csv", index=False)
