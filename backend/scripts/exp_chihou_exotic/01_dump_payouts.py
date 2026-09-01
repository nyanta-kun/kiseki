import os, psycopg2, pandas as pd
from dotenv import load_dotenv
load_dotenv("/Users/ysuzuki/GitHub/kiseki/.env")
dsn=(f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} dbname={os.getenv('DB_NAME')} "
     f"user={os.getenv('DB_USER')} password={os.getenv('DB_PASSWORD')}")
conn=psycopg2.connect(dsn)
sql = """
with top3 as (
  select rr.race_id,
         max(case when rr.finish_position=1 then rr.win_popularity end) p1,
         max(case when rr.finish_position=2 then rr.win_popularity end) p2,
         max(case when rr.finish_position=3 then rr.win_popularity end) p3,
         max(case when rr.finish_position=1 then rr.win_odds end) o1,
         max(case when rr.finish_position=2 then rr.win_odds end) o2,
         max(case when rr.finish_position=3 then rr.win_odds end) o3
  from chihou.race_results rr
  where rr.finish_position between 1 and 3
  group by rr.race_id
), pay as (
  select race_id,
         max(case when bet_type='trio' then payout end) trio,
         max(case when bet_type='trifecta' then payout end) tri,
         max(case when bet_type='win' then payout end) winp,
         max(case when bet_type='quinella' then payout end) quin,
         max(case when bet_type='wide' then payout end) wide_max,
         min(case when bet_type='wide' then payout end) wide_min
  from chihou.race_payouts group by race_id
)
select r.id race_id, r.date, r.course, r.course_name, r.head_count, r.distance, r.grade,
       r.condition, r.track_type, t.p1,t.p2,t.p3, t.o1,t.o2,t.o3,
       pay.trio, pay.tri, pay.winp, pay.quin, pay.wide_max, pay.wide_min
from chihou.races r
join top3 t on t.race_id=r.id
join pay on pay.race_id=r.id
where r.date >= '20240101'
"""
df = pd.read_sql(sql, conn)
df.to_pickle("/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/exotic.pkl")
print(df.shape)
print(df.head())
print(df[['trio','tri']].describe())
