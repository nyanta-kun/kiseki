SELECT n_entries, count(*), count(*) FILTER (WHERE gap = 0) AS gap_zero,
       round(avg(gap)::numeric,4) AS gap_avg
FROM keirin.race_shapes WHERE race_date >= '2026-09-01' GROUP BY 1 ORDER BY 1;
