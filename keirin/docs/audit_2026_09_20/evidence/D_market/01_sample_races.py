"""D_market: 監査対象レースの無作為抽出（wt_races は10万行程度なので全走査ではない）。
2024-01〜2026-08 の各月から n_entries=7,9 を中心に無作為抽出し、race_key リストを保存する。
"""
import os
import random
import psycopg2
import psycopg2.extras
import pandas as pd

DB_URL = os.environ["KEIRIN_DB_URL"]
OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/D_market"

random.seed(20260920)

conn = psycopg2.connect(DB_URL)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# wt_races は104,476行の小テーブル。日付範囲を絞って一括取得(全走査ではなく該当行のみ)。
cur.execute("""
    SELECT race_key, venue_id, race_date, race_no, grade, race_type, n_entries, cup_grade
    FROM keirin.wt_races
    WHERE status = 3 AND cancel = 0
      AND race_date >= '2024-01-01' AND race_date < '2026-09-01'
      AND n_entries IN (5,6,7,8,9)
""")
rows = cur.fetchall()
df = pd.DataFrame(rows)
df["race_date"] = pd.to_datetime(df["race_date"])
df["ym"] = df["race_date"].dt.strftime("%Y-%m")
print("total finished races 2024-01..2026-08:", len(df))
print(df.groupby(["ym", "n_entries"]).size().unstack(fill_value=0))

# 月ごと・車立てごとに無作為抽出
PER_MONTH_N = {7: 150, 9: 150, 6: 40, 5: 15, 8: 10}

sampled = []
for ym, g in df.groupby("ym"):
    for n_entries, gg in g.groupby("n_entries"):
        k = PER_MONTH_N.get(n_entries, 0)
        if k <= 0:
            continue
        take = gg.sample(n=min(k, len(gg)), random_state=hash((ym, n_entries)) % (2**31))
        sampled.append(take)

sample_df = pd.concat(sampled, ignore_index=True)
print("sampled races total:", len(sample_df))
print(sample_df.groupby(["ym", "n_entries"]).size().unstack(fill_value=0))

sample_df.to_pickle(f"{OUT}/sample_races.pkl")
sample_df.to_csv(f"{OUT}/sample_races.csv", index=False)
cur.close()
conn.close()
