#!/usr/bin/env python3
"""同一開催で前日と当日の選手がどれだけ重なるか（決着傾向＝出走構成の反映、の裏付け）。"""
import os, pandas as pd, psycopg2
c = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
d = pd.read_sql("""
with p as (select r.venue_id v, r.race_date d, r.cup_id, e.player_id
           from keirin.wt_entries e join keirin.wt_races r using(race_key)
           where r.race_date >= '2025-01-01' and coalesce(r.cancel,0)=0 group by 1,2,3,4),
 t as (select a.v, a.d, a.cup_id, count(*) n, sum(case when b.player_id is not null then 1 else 0 end) n_prev
       from p a left join p b on b.v=a.v and b.cup_id=a.cup_id and b.d = (a.d::date - 1)::text and b.player_id=a.player_id
       group by 1,2,3)
select t.*, (select max(day_index) from keirin.wt_races r where r.venue_id=t.v and r.race_date=t.d) day_index from t
""", c)
d = d[d.day_index >= 2]
print("会場×日 (day_index>=2, 2025-01〜):", len(d))
print("前日も同開催に出走していた選手の割合: 平均 %.3f  中央 %.3f  p10 %.3f" % ((d.n_prev / d.n).mean(), (d.n_prev / d.n).median(), (d.n_prev / d.n).quantile(0.1)))
print(d.groupby("day_index").apply(lambda g: pd.Series({"days": len(g), "carry": (g.n_prev / g.n).mean()})).round(3))
