"""発走6分前の単勝オッズスナップショット（実運用と同条件）を抜く。"""
import os, psycopg2, pandas as pd
from dotenv import load_dotenv
load_dotenv("/Users/ysuzuki/GitHub/kiseki/.env")
dsn=(f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} dbname={os.getenv('DB_NAME')} "
     f"user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
conn=psycopg2.connect(dsn)
SQL = """
SELECT r.id AS race_id, e.horse_number, o.odds, o.fetched_at
FROM chihou.races r
JOIN chihou.race_entries e ON e.race_id = r.id AND e.horse_number IS NOT NULL
CROSS JOIN LATERAL (
    SELECT oh.odds, oh.fetched_at
    FROM chihou.odds_history oh
    WHERE oh.race_id = r.id
      AND oh.bet_type = 'win'
      AND oh.combination = e.horse_number::text
      AND oh.fetched_at <= (
            to_timestamp(r.date || r.post_time, 'YYYYMMDDHH24MI')
            - interval '9 hours' - interval '6 minutes')
    ORDER BY oh.fetched_at DESC
    LIMIT 1
) o
WHERE r.date >= %s AND r.date < %s
  AND r.course <> '83' AND r.post_time ~ '^[0-9]{4}$'
"""
months=[("20260407","20260501"),("20260501","20260601"),("20260601","20260701"),
        ("20260701","20260801"),("20260801","20260901")]
out=[]
for a,b in months:
    df=pd.read_sql(SQL, conn, params=(a,b))
    print(a, df.shape, df.race_id.nunique(), flush=True)
    out.append(df)
d=pd.concat(out)
d.to_pickle("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/preodds.pkl")
print("total", d.shape, d.race_id.nunique())
