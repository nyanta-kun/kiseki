"""Task4: 同一レース内での券種間オッズの関係。
- 三連単(6順列)の合成オッズ vs 三連複オッズ
- 二車単(2順列)の合成オッズ vs 二車複オッズ
- ワイド vs 三連複 (相対テイクアウト比較)
"""
import itertools
import pickle
import numpy as np
import pandas as pd

OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/D_market"
RNG = np.random.default_rng(20260922)

with open(f"{OUT}/dataset.pkl", "rb") as f:
    D = pickle.load(f)
odds = D["odds"].dropna(subset=["odds_value"]).copy()

# race_key, n_entries, year のマップ
meta = odds.drop_duplicates("race_key")[["race_key", "n_entries", "year"]].set_index("race_key")


def pivot(bt):
    g = odds[odds["bet_type"] == bt]
    return g.pivot_table(index="race_key", columns="combination", values="odds_value", aggfunc="first")


trifecta_p = pivot("trifecta")
trio_p = pivot("trio")
exacta_p = pivot("exacta")
quinella_p = pivot("quinella")
wide_p = pivot("quinellaPlace")

print("pivots built:", trifecta_p.shape, trio_p.shape, exacta_p.shape, quinella_p.shape, wide_p.shape)

# --- 三連単(6順列) vs 三連複 ---
rows = []
common_races = trifecta_p.index.intersection(trio_p.index)
for race_key in common_races:
    n_entries = meta.loc[race_key, "n_entries"]
    if n_entries not in (7, 9):
        continue
    cars = range(1, int(n_entries) + 1)
    trio_row = trio_p.loc[race_key]
    tri_row = trifecta_p.loc[race_key]
    for combo in itertools.combinations(cars, 3):
        trio_label = "=".join(map(str, combo))
        trio_odds = trio_row.get(trio_label, np.nan)
        if pd.isna(trio_odds):
            continue
        inv_sum = 0.0
        ok = True
        for perm in itertools.permutations(combo):
            lab = "-".join(map(str, perm))
            o = tri_row.get(lab, np.nan)
            if pd.isna(o) or o <= 0:
                ok = False
                break
            inv_sum += 1.0 / o
        if not ok or inv_sum <= 0:
            continue
        synth_trio = 1.0 / inv_sum
        ratio = trio_odds / synth_trio  # >1: 三連複の方が高倍率(得), <1: 三連単合成の方が高倍率
        rows.append((race_key, n_entries, trio_odds, synth_trio, ratio))

cmp_df = pd.DataFrame(rows, columns=["race_key", "n_entries", "trio_odds", "synth_trio_from_trifecta", "ratio"])
cmp_df.to_csv(f"{OUT}/task4_trifecta_vs_trio_combo_level.csv", index=False)
print("\n===== TASK4-a: 三連複 / (三連単6順列の合成) 比率 =====")
for n_entries, g in cmp_df.groupby("n_entries"):
    print(n_entries, "車: n_combo=", len(g), "median ratio=", round(g["ratio"].median(), 4),
          "mean=", round(g["ratio"].mean(), 4),
          "IQR=[", round(g["ratio"].quantile(0.25), 4), ",", round(g["ratio"].quantile(0.75), 4), "]")

# レース単位で中央値をとった上でのブートストラップCI
race_level = cmp_df.groupby(["race_key", "n_entries"])["ratio"].median().reset_index()
for n_entries, g in race_level.groupby("n_entries"):
    v = g["ratio"].to_numpy()
    n = len(v)
    boot = np.array([np.median(v[RNG.integers(0, n, n)]) for _ in range(2000)])
    print(n_entries, "車 (レース単位中央値の中央値):", round(np.median(v), 4),
          "95%CI=[", round(np.percentile(boot, 2.5), 4), ",", round(np.percentile(boot, 97.5), 4), "] n_races=", n)

# --- 二車単(2順列) vs 二車複 ---
rows = []
common_races2 = exacta_p.index.intersection(quinella_p.index)
for race_key in common_races2:
    n_entries = meta.loc[race_key, "n_entries"]
    if n_entries not in (7, 9):
        continue
    cars = range(1, int(n_entries) + 1)
    q_row = quinella_p.loc[race_key]
    e_row = exacta_p.loc[race_key]
    for combo in itertools.combinations(cars, 2):
        q_label = "=".join(map(str, combo))
        q_odds = q_row.get(q_label, np.nan)
        if pd.isna(q_odds):
            continue
        o1 = e_row.get(f"{combo[0]}-{combo[1]}", np.nan)
        o2 = e_row.get(f"{combo[1]}-{combo[0]}", np.nan)
        if pd.isna(o1) or pd.isna(o2) or o1 <= 0 or o2 <= 0:
            continue
        synth_q = 1.0 / (1.0 / o1 + 1.0 / o2)
        ratio = q_odds / synth_q
        rows.append((race_key, n_entries, q_odds, synth_q, ratio))
cmp2_df = pd.DataFrame(rows, columns=["race_key", "n_entries", "quinella_odds", "synth_quinella_from_exacta", "ratio"])
cmp2_df.to_csv(f"{OUT}/task4_exacta_vs_quinella_combo_level.csv", index=False)
print("\n===== TASK4-b: 二車複 / (二車単2順列の合成) 比率 =====")
for n_entries, g in cmp2_df.groupby("n_entries"):
    print(n_entries, "車: n_combo=", len(g), "median ratio=", round(g["ratio"].median(), 4),
          "mean=", round(g["ratio"].mean(), 4))

# --- ワイド vs 三連複 (相対テイクアウト比較) ---
# O_wide(ab) * sum_c(1/O_trio(abc)) ~= (1-t_wide)/(1-t_trio)
rows = []
common_races3 = wide_p.index.intersection(trio_p.index)
for race_key in common_races3:
    n_entries = meta.loc[race_key, "n_entries"]
    if n_entries not in (7, 9):
        continue
    cars = list(range(1, int(n_entries) + 1))
    w_row = wide_p.loc[race_key]
    t_row = trio_p.loc[race_key]
    for a, b in itertools.combinations(cars, 2):
        w_label = f"{a}={b}"
        w_odds = w_row.get(w_label, np.nan)
        if pd.isna(w_odds):
            continue
        inv_sum = 0.0
        ok = True
        for c in cars:
            if c in (a, b):
                continue
            trio_label = "=".join(map(str, sorted((a, b, c))))
            o = t_row.get(trio_label, np.nan)
            if pd.isna(o) or o <= 0:
                ok = False
                break
            inv_sum += 1.0 / o
        if not ok or inv_sum <= 0:
            continue
        rel = w_odds * inv_sum  # ~= (1-t_wide)/(1-t_trio)
        rows.append((race_key, n_entries, w_odds, inv_sum, rel))
cmp3_df = pd.DataFrame(rows, columns=["race_key", "n_entries", "wide_odds", "sum_inv_trio", "rel_takeout_ratio"])
cmp3_df.to_csv(f"{OUT}/task4_wide_vs_trio_combo_level.csv", index=False)
print("\n===== TASK4-c: ワイド odds * Σ(1/三連複odds) ~= (1-tワイド)/(1-t三連複) =====")
for n_entries, g in cmp3_df.groupby("n_entries"):
    print(n_entries, "車: n_combo=", len(g), "median=", round(g["rel_takeout_ratio"].median(), 4),
          "mean=", round(g["rel_takeout_ratio"].mean(), 4))
print("(値>1: ワイドの方が三連複より割安(控除率が低い) / <1: ワイドの方が割高)")

print("\n[task4 done]")
