with o as (
  select s.race_key, string_to_array(replace(s.combination,'=','-'),'-')::int[] c, 1.0/s.odds_value q
  from keirin.wt_odds_snapshot s where s.snapshot_type='morning' and s.bet_type='trifecta' and s.odds_value>0
    and s.race_key between '20260601' and '20261028'
), k as (select race_key, sum(q) s from o group by 1 having count(*)>=200)
select o.race_key, x.car frame_no, sum(o.q)/k.s m3_am
from o join k using (race_key), lateral unnest(o.c) x(car) group by 1,2,k.s order by 1,2;
