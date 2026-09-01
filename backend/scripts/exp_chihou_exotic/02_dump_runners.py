import os, psycopg2, pandas as pd
from dotenv import load_dotenv
load_dotenv("/Users/ysuzuki/GitHub/kiseki/.env")
dsn=(f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} dbname={os.getenv('DB_NAME')} "
     f"user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
conn=psycopg2.connect(dsn)
sql = """
select rr.race_id, rr.horse_number, rr.finish_position, rr.win_odds, rr.win_popularity,
       rr.abnormality_code
from chihou.race_results rr
join chihou.races r on r.id = rr.race_id
where r.date >= '20240101'
"""
df = pd.read_sql(sql, conn)
df.to_pickle("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/runners.pkl")
print(df.shape, df.race_id.nunique())
print(df.win_odds.isna().mean(), df.finish_position.isna().mean())
