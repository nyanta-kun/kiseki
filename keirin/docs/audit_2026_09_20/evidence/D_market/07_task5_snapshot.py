"""Task5: 確定オッズの「最終」性 + 朝〜締切のオッズ変動。
wt_odds_snapshot は 2026-06-08 以降のみ存在(実測)。その制約の中で少数レースを見る。
DB へは1回だけ小さく問い合わせる(race_keyをIN指定、月は絞らない=既に小さいサンプルのため)。
"""
import os
import time
import pickle
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/D_market"
DB_URL = os.environ["KEIRIN_DB_URL"]
RNG = np.random.default_rng(1)

conn = psycopg2.connect(DB_URL)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# 2026-06-08以降で status=3, cancel=0 のレースから無作為に200件(7,9車)
cur.execute("""
    SELECT race_key, n_entries FROM keirin.wt_races
    WHERE status=3 AND cancel=0 AND race_date >= '2026-06-08' AND race_date < '2026-09-15'
      AND n_entries IN (7,9)
""")
rows = cur.fetchall()
df = pd.DataFrame(rows)
print("candidates:", len(df))
sample = df.groupby("n_entries", group_keys=False).apply(
    lambda g: g.sample(n=min(150, len(g)), random_state=42))
keys = sample["race_key"].tolist()
print("sampled:", len(keys))

time.sleep(2)
cur.execute(
    "SELECT race_key, bet_type, combination, odds_value, snapshot_type, snapshot_at "
    "FROM keirin.wt_odds_snapshot WHERE race_key = ANY(%s) AND bet_type IN ('trifecta','trio')",
    (keys,),
)
snap_rows = cur.fetchall()
snap_df = pd.DataFrame(snap_rows)
print("snapshot rows:", len(snap_df))
snap_df.to_pickle(f"{OUT}/task5_snapshot_raw.pkl")

cur.close()
conn.close()

# --- 分析: 最人気(favorite)がスナップショットごとに変わるか ---
SNAP_ORDER = ["h06", "morning", "h10", "h12", "h14", "evening", "h18", "h20"]
snap_df["snapshot_type"] = pd.Categorical(snap_df["snapshot_type"], categories=SNAP_ORDER, ordered=True)

results = []
for (race_key, bt), g in snap_df.groupby(["race_key", "bet_type"]):
    piv = g.pivot_table(index="combination", columns="snapshot_type", values="odds_value", aggfunc="first")
    present = [c for c in SNAP_ORDER if c in piv.columns and piv[c].notna().sum() > 0]
    if len(present) < 2:
        continue
    first_snap = present[0]
    last_snap = present[-1]
    fav_first = piv[first_snap].idxmin()
    fav_last = piv[last_snap].idxmin()
    # 中央変動率(1着オッズ、全目のoddsの変化率の中央値)
    both = piv[[first_snap, last_snap]].dropna()
    if len(both) == 0:
        continue
    pct_change = ((both[last_snap] - both[first_snap]) / both[first_snap]).abs()
    results.append(dict(race_key=race_key, bet_type=bt, first_snap=first_snap, last_snap=last_snap,
                         fav_same=(fav_first == fav_last),
                         median_abs_pct_change=pct_change.median(),
                         n_combo=len(both)))

res_df = pd.DataFrame(results)
res_df.to_csv(f"{OUT}/task5_freshness_summary.csv", index=False)
print("\n===== TASK5: 朝〜最終のオッズ変動 =====")
for bt, g in res_df.groupby("bet_type"):
    print(bt, "n_races=", len(g), "fav_same_rate=", round(g["fav_same"].mean(), 4),
          "median |pct_change| (median across races)=", round(g["median_abs_pct_change"].median(), 4))

print("\nsnapshot_type available per race (sample):")
print(snap_df.groupby("race_key")["snapshot_type"].apply(lambda s: sorted(set(s.dropna().astype(str)))).head(10))

print("\n[task5 done]")
