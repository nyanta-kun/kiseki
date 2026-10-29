\pset footer off
with races as (
  select race_key from keirin.wt_entries where race_key >= :'d0' and race_key < :'d1'
  group by race_key having count(*)=7 and bool_and(finish_order is not null)
), o as (
  select o.race_key, string_to_array(replace(o.combination,'=','-'),'-')::int[] c, 1.0/o.odds_value q
  from keirin.wt_odds o join races using (race_key) where o.bet_type='trifecta' and o.odds_value>0
), okr as (select race_key, sum(q) s, count(*) n from o group by 1 having count(*)>=200),
mk as (
  select o.race_key, x.car, sum(o.q)/k.s m3, sum(case when o.c[1]=x.car then o.q else 0 end)/k.s mw
  from o join okr k using (race_key), lateral unnest(o.c) x(car) group by 1,2,k.s
)
select e.race_key, r.race_date, r.venue_id, r.race_no, r.cup_id, r.day_index, r.grade, r.cup_grade, r.race_type, r.distance,
  e.frame_no, e.player_id, e.player_class, e.term, e.gear_ratio, e.style, e.race_point, e.prediction_mark,
  e.s_count, e.h_count, e.b_count, e.front_runner, e.stalker, e.deep_closer, e.marker, e.first_rate, e.second_rate, e.third_rate,
  e.ex_spurt_pct, e.ex_thrust_pct, e.ex_left_behind_pct, e.ex_split_line_pct, e.ex_snatch_pct,
  e.line_group, e.line_size, e.line_pos, e.is_line_leader, e.n_lines, e.finish_order, m.m3, m.mw
from keirin.wt_entries e join okr using (race_key) join keirin.wt_races r on r.race_key=e.race_key
join mk m on m.race_key=e.race_key and m.car=e.frame_no
order by e.race_key, e.frame_no;
