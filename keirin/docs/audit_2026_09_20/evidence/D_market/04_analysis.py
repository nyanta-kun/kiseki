"""D_market 分析本体（DB非接続・ローカル dataset.pkl のみ使用）。
出力: CSV群 + 標準出力に主要な表。
"""
import itertools
import pickle
import re
import numpy as np
import pandas as pd

OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/D_market"
RNG = np.random.default_rng(20260920)

with open(f"{OUT}/dataset.pkl", "rb") as f:
    D = pickle.load(f)
odds = D["odds"]
sample = D["sample"]

BET_JP = {"trifecta": "三連単", "trio": "三連複", "exacta": "二車単",
          "quinella": "二車複", "quinellaPlace": "ワイド"}
BANDS = [(0, 5), (5, 10), (10, 20), (20, 50), (50, 100), (100, 300), (300, np.inf)]
BAND_LABEL = ["~5", "5~10", "10~20", "20~50", "50~100", "100~300", "300+"]


def band_of(o):
    for (lo, hi), lab in zip(BANDS, BAND_LABEL):
        if lo <= o < hi:
            return lab
    return None


odds = odds.dropna(subset=["odds_value", "win_flag"]).copy()
odds["band"] = odds["odds_value"].map(band_of)

# ============================================================
# Task 1: 券種別の実効控除率
# ============================================================
print("\n===== TASK1: 券種別 実効控除率 =====")


def implied_takeout_by_race(g):
    s = (1.0 / g["odds_value"]).sum()
    return pd.Series({"sum_inv_odds": s, "n_combo": len(g)})


rows = []
for (bt, n_entries), g in odds.groupby(["bet_type", "n_entries"]):
    per_race = g.groupby("race_key").apply(implied_takeout_by_race)
    per_race["implied_takeout"] = 1 - 1 / per_race["sum_inv_odds"]

    # 払戻ベース: そのレースの全目均等買いの回収率
    pay = g.groupby("race_key").apply(
        lambda gg: gg.loc[gg["win_flag"] == True, "payout100"].sum() / (100 * len(gg))
    )
    n_races = per_race.shape[0]
    boot = []
    idx = pay.index.to_numpy()
    payvals = pay.to_numpy()
    for _ in range(2000):
        s = RNG.integers(0, len(payvals), len(payvals))
        boot.append(payvals[s].mean())
    boot = np.array(boot)
    rows.append(dict(
        bet_type=bt, bet_jp=BET_JP[bt], n_entries=n_entries, n_races=n_races,
        mean_sum_inv_odds=per_race["sum_inv_odds"].mean(),
        implied_takeout_mean=per_race["implied_takeout"].mean(),
        realized_return_mean=payvals.mean(),
        realized_return_ci_lo=np.percentile(boot, 2.5),
        realized_return_ci_hi=np.percentile(boot, 97.5),
        realized_takeout_mean=1 - payvals.mean(),
    ))

task1 = pd.DataFrame(rows).sort_values(["bet_type", "n_entries"])
task1.to_csv(f"{OUT}/task1_takeout_by_bettype_nentries.csv", index=False)
print(task1.to_string(index=False))

# 年別
rows = []
for (bt, year), g in odds.groupby(["bet_type", "year"]):
    per_race = g.groupby("race_key").apply(implied_takeout_by_race)
    per_race["implied_takeout"] = 1 - 1 / per_race["sum_inv_odds"]
    pay = g.groupby("race_key").apply(
        lambda gg: gg.loc[gg["win_flag"] == True, "payout100"].sum() / (100 * len(gg))
    )
    rows.append(dict(bet_type=bt, bet_jp=BET_JP[bt], year=year, n_races=len(pay),
                      implied_takeout_mean=per_race["implied_takeout"].mean(),
                      realized_return_mean=pay.mean()))
task1y = pd.DataFrame(rows).sort_values(["bet_type", "year"])
task1y.to_csv(f"{OUT}/task1_takeout_by_bettype_year.csv", index=False)
print("\n-- 年別 --")
print(task1y.to_string(index=False))

# ============================================================
# Task 2: オッズ帯別の無情報回収率(favorite-longshot bias)
# ============================================================
print("\n===== TASK2: オッズ帯別 無情報回収率 =====")


def band_return_bootstrap(g, n_boot=2000):
    # レース単位で「その帯の全目均等買い」のレース単位リターンを作る
    per_race = g.groupby("race_key").apply(
        lambda gg: pd.Series({
            "ret": gg.loc[gg["win_flag"] == True, "payout100"].sum() / (100 * len(gg)),
            "n": len(gg),
        })
    )
    vals = per_race["ret"].to_numpy()
    n = len(vals)
    if n == 0:
        return None
    boot = np.array([vals[RNG.integers(0, n, n)].mean() for _ in range(n_boot)])
    return dict(n_races=n, mean_return=vals.mean(),
                ci_lo=np.percentile(boot, 2.5), ci_hi=np.percentile(boot, 97.5))


rows = []
for bt, g0 in odds.groupby("bet_type"):
    for n_entries, g1 in g0.groupby("n_entries"):
        if n_entries not in (7, 9):
            continue
        for band in BAND_LABEL:
            g2 = g1[g1["band"] == band]
            if g2.empty:
                continue
            r = band_return_bootstrap(g2)
            if r is None:
                continue
            rows.append(dict(bet_type=bt, bet_jp=BET_JP[bt], n_entries=n_entries, band=band, **r))
task2 = pd.DataFrame(rows)
task2.to_csv(f"{OUT}/task2_band_return.csv", index=False)
print(task2.to_string(index=False))

# 年別安定性(trifecta/trioのみ, 7車)
rows = []
for bt in ["trifecta", "trio"]:
    g0 = odds[(odds["bet_type"] == bt) & (odds["n_entries"] == 7)]
    for year, g1 in g0.groupby("year"):
        for band in BAND_LABEL:
            g2 = g1[g1["band"] == band]
            if g2.empty or g2["race_key"].nunique() < 20:
                continue
            r = band_return_bootstrap(g2, n_boot=1000)
            rows.append(dict(bet_type=bt, year=year, band=band, **r))
task2y = pd.DataFrame(rows)
task2y.to_csv(f"{OUT}/task2_band_return_by_year_7car.csv", index=False)
print("\n-- 年別(7車) --")
print(task2y.to_string(index=False))

print("\n[task1,task2 done]")
