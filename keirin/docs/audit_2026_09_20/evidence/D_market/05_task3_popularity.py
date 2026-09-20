"""Task3: 人気順の成績。人気1位の的中率、人気上位k点の的中率とROI(均等/ダッチ)。"""
import pickle
import numpy as np
import pandas as pd

OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/D_market"
RNG = np.random.default_rng(20260921)

with open(f"{OUT}/dataset.pkl", "rb") as f:
    D = pickle.load(f)
odds = D["odds"].dropna(subset=["odds_value", "win_flag"]).copy()

BET_JP = {"trifecta": "三連単", "trio": "三連複", "exacta": "二車単",
          "quinella": "二車複", "quinellaPlace": "ワイド"}


def per_race_rank(g):
    g = g.sort_values("odds_value").reset_index(drop=True)
    g["pop_rank"] = np.arange(1, len(g) + 1)
    return g


# 人気1位の的中率(bet_type別、n_entries別)
rows = []
for bt, g0 in odds.groupby("bet_type"):
    for n_entries, g1 in g0.groupby("n_entries"):
        if n_entries not in (6, 7, 9):
            continue
        ranked = g1.groupby("race_key", group_keys=False).apply(per_race_rank)
        fav = ranked[ranked["pop_rank"] == 1]
        hit_rate = fav["win_flag"].mean()
        n = len(fav)
        boot = np.array([fav["win_flag"].to_numpy()[RNG.integers(0, n, n)].mean() for _ in range(2000)])
        rows.append(dict(bet_type=bt, bet_jp=BET_JP[bt], n_entries=n_entries, n_races=n,
                          fav1_hit_rate=hit_rate,
                          ci_lo=np.percentile(boot, 2.5), ci_hi=np.percentile(boot, 97.5),
                          fav1_mean_odds=fav["odds_value"].mean()))
task3_fav1 = pd.DataFrame(rows)
task3_fav1.to_csv(f"{OUT}/task3_favorite1_hitrate.csv", index=False)
print("===== TASK3-a: 人気1位の的中率 =====")
print(task3_fav1.to_string(index=False))

# 人気上位k点(k=1..15) 的中率・ROI(均等/ダッチ) : trifecta, 7車 と 9車
print("\n===== TASK3-b: 人気上位k点 (trifecta) =====")
results = []
for n_entries in (7, 9):
    g0 = odds[(odds["bet_type"] == "trifecta") & (odds["n_entries"] == n_entries)]
    ranked = g0.groupby("race_key", group_keys=False).apply(per_race_rank)
    max_k = 20
    race_keys = ranked["race_key"].unique()
    n_races = len(race_keys)
    # 事前にレースごとのpop_rank<=max_k分だけを配列化
    sub = ranked[ranked["pop_rank"] <= max_k]
    for k in range(1, max_k + 1):
        topk = sub[sub["pop_rank"] <= k]
        # レースごとに: 当たったか、払戻(均等100円/点)、ダッチ(1/odds比例、合計100k円想定)
        def agg(gg):
            hit = gg["win_flag"].any()
            flat_bet = 100 * len(gg)
            flat_payout = gg.loc[gg["win_flag"] == True, "payout100"].sum()
            # ダッチ: 予算 = 100*k 円、各点のstakeは 1/odds に比例するように配分
            inv = 1.0 / gg["odds_value"]
            budget = 100 * k
            stakes = inv / inv.sum() * budget
            # 100円単位に丸め(現実のダッチは100円単位が多いが、ここは連続値で近似)
            dutch_payout = (stakes * gg["odds_value"] * gg["win_flag"]).sum()
            return pd.Series(dict(hit=hit, flat_bet=flat_bet, flat_payout=flat_payout,
                                   dutch_bet=budget, dutch_payout=dutch_payout, n=len(gg)))
        agg_df = topk.groupby("race_key").apply(agg)
        n_r = len(agg_df)
        hit_rate = agg_df["hit"].mean()
        flat_roi = agg_df["flat_payout"].sum() / agg_df["flat_bet"].sum()
        dutch_roi = agg_df["dutch_payout"].sum() / agg_df["dutch_bet"].sum()
        # bootstrap CI (race-level) for hit_rate and flat_roi
        hv = agg_df["hit"].to_numpy().astype(float)
        fb = agg_df["flat_bet"].to_numpy(); fp = agg_df["flat_payout"].to_numpy()
        boot_hit = []
        boot_roi = []
        for _ in range(1000):
            idx = RNG.integers(0, n_r, n_r)
            boot_hit.append(hv[idx].mean())
            boot_roi.append(fp[idx].sum() / fb[idx].sum())
        results.append(dict(n_entries=n_entries, k=k, n_races=n_r, hit_rate=hit_rate,
                             hit_ci_lo=np.percentile(boot_hit, 2.5), hit_ci_hi=np.percentile(boot_hit, 97.5),
                             flat_roi=flat_roi, flat_roi_ci_lo=np.percentile(boot_roi, 2.5),
                             flat_roi_ci_hi=np.percentile(boot_roi, 97.5),
                             dutch_roi=dutch_roi,
                             avg_cost_flat=fb.mean()))
task3_topk = pd.DataFrame(results)
task3_topk.to_csv(f"{OUT}/task3_topk_trifecta.csv", index=False)
print(task3_topk.to_string(index=False))

# 30%的中に必要な点数(trifecta) を表示
print("\n-- 的中率30%に必要な人気上位点数 (trifecta) --")
for n_entries in (7, 9):
    sub = task3_topk[task3_topk["n_entries"] == n_entries]
    over30 = sub[sub["hit_rate"] >= 0.30]
    if len(over30):
        row = over30.iloc[0]
        print(n_entries, "車:", "k=", int(row["k"]), "hit_rate=", round(row["hit_rate"], 4),
              "flat_roi=", round(row["flat_roi"], 4), "dutch_roi=", round(row["dutch_roi"], 4))
    else:
        print(n_entries, "車: k<=20 では30%に到達せず。最大k=20 hit_rate=",
              round(sub.iloc[-1]["hit_rate"], 4))

print("\n[task3 done]")
