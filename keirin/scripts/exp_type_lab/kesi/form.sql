select e.player_id, r.race_date, e.race_key, r.cup_id, r.day_index, e.finish_order
from keirin.wt_entries e join keirin.wt_races r using (race_key)
where e.race_key >= '20240101' and e.finish_order is not null and e.player_id is not null order by 1,2,3;
