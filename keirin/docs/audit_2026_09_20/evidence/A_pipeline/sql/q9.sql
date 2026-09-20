SELECT count(*) AS rows,
       count(*) FILTER (WHERE cup_id IS NULL) AS cup_id_null,
       count(*) FILTER (WHERE day_index IS NULL) AS day_index_null
FROM keirin.wt_races WHERE race_date >= '2026-01-01' AND race_date < '2026-09-01';
