"""旧文書「三連単÷三連複は中央2.5倍」の直接再現: 実際に的中した三連単(着順そのまま)odds ÷
   同じ3車の三連複oddsの比率。同着があるレースは除く(単純化)。"""
import pickle
import numpy as np
import pandas as pd

OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/D_market"
with open(f"{OUT}/dataset.pkl", "rb") as f:
    D = pickle.load(f)
odds = D["odds"]
rr = D["race_results"]

rows = []
for race_key, info in rr.items():
    if info["n_finishers_top3"] != 3:
        continue
    trif_wins = list(info["trifecta"])
    trio_wins = list(info["trio"])
    if len(trif_wins) != 1 or len(trio_wins) != 1:
        continue  # 同着は除く
    rows.append((race_key, trif_wins[0], trio_wins[0]))
win_df = pd.DataFrame(rows, columns=["race_key", "trifecta_combo", "trio_combo"])

tri_odds = odds[odds.bet_type == "trifecta"].set_index(["race_key", "combination"])["odds_value"]
trio_odds = odds[odds.bet_type == "trio"].set_index(["race_key", "combination"])["odds_value"]
meta = odds.drop_duplicates("race_key").set_index("race_key")[["n_entries"]]

recs = []
for r in win_df.itertuples():
    try:
        to = tri_odds.loc[(r.race_key, r.trifecta_combo)]
        qo = trio_odds.loc[(r.race_key, r.trio_combo)]
    except KeyError:
        continue
    n_entries = meta.loc[r.race_key, "n_entries"]
    recs.append((r.race_key, n_entries, to, qo, to / qo))
res = pd.DataFrame(recs, columns=["race_key", "n_entries", "trifecta_odds_win", "trio_odds_win", "ratio"])
res.to_csv(f"{OUT}/task4d_winner_order_ratio.csv", index=False)
print("n =", len(res))
for n_entries, g in res.groupby("n_entries"):
    if n_entries not in (7, 9):
        continue
    print(n_entries, "車: n=", len(g), "median ratio(trifecta_win/trio_win)=", round(g["ratio"].median(), 3),
          "mean=", round(g["ratio"].mean(), 3),
          "IQR=[", round(g["ratio"].quantile(.25), 3), ",", round(g["ratio"].quantile(.75), 3), "]")

# 「順当帯」= trio_odds_win が小さい(favorite通り決着)側 vs 大きい側
res7 = res[res["n_entries"] == 7]
low = res7[res7["trio_odds_win"] <= res7["trio_odds_win"].median()]
high = res7[res7["trio_odds_win"] > res7["trio_odds_win"].median()]
print("\n7車 順当帯(trio_odds_win<=中央値):median ratio=", round(low["ratio"].median(), 3), "n=", len(low))
print("7車 波乱帯(trio_odds_win>中央値):median ratio=", round(high["ratio"].median(), 3), "n=", len(high))
