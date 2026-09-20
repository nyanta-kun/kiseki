"""月ごとに race_key の IN 指定で wt_odds / wt_entries / wt_race_payouts を取得しローカル保存。
1クエリ=1か月分、直列実行、クエリ間に sleep。"""
import os
import time
import psycopg2
import psycopg2.extras
import pandas as pd

DB_URL = os.environ["KEIRIN_DB_URL"]
OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/D_market"

sample_df = pd.read_pickle(f"{OUT}/sample_races.pkl")
months = sorted(sample_df["ym"].unique())

conn = psycopg2.connect(DB_URL)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

os.makedirs(f"{OUT}/odds_by_month", exist_ok=True)
os.makedirs(f"{OUT}/entries_by_month", exist_ok=True)

for i, ym in enumerate(months):
    keys = sample_df.loc[sample_df["ym"] == ym, "race_key"].tolist()
    odds_path = f"{OUT}/odds_by_month/{ym}.pkl"
    ent_path = f"{OUT}/entries_by_month/{ym}.pkl"
    if os.path.exists(odds_path) and os.path.exists(ent_path):
        print(f"skip {ym} (exists)")
        continue
    t0 = time.time()
    cur.execute(
        "SELECT race_key, bet_type, combination, odds_value FROM keirin.wt_odds "
        "WHERE race_key = ANY(%s)",
        (keys,),
    )
    odds_rows = cur.fetchall()
    odds_df = pd.DataFrame(odds_rows)
    odds_df.to_pickle(odds_path)

    time.sleep(2)

    cur.execute(
        "SELECT race_key, frame_no, player_id, finish_order, line_group, line_pos, "
        "n_lines, is_line_leader, pred_win_pct, pred_top3_pct, pred_top2_pct "
        "FROM keirin.wt_entries WHERE race_key = ANY(%s)",
        (keys,),
    )
    ent_rows = cur.fetchall()
    ent_df = pd.DataFrame(ent_rows)
    ent_df.to_pickle(ent_path)

    dt = time.time() - t0
    print(f"{ym}: odds={len(odds_df)} entries={len(ent_df)} ({dt:.1f}s)")
    time.sleep(3)

cur.close()
conn.close()
print("done")
