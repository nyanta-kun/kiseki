SELECT e.race_key, r.n_entries, count(*) AS n,
       round(sum(e.race_point)::numeric,1) AS rp_sum,
       round(avg(e.race_point)::numeric,1) AS rp_avg
FROM keirin.wt_entries e JOIN keirin.wt_races r USING(race_key)
WHERE r.race_date = '2026-06-12' AND substr(e.race_key,10,2) IN ('43','61')
GROUP BY 1,2 ORDER BY 1 LIMIT 12;
-- 比較: 同じ会場の翌日
SELECT e.race_key, r.n_entries, round(sum(e.race_point)::numeric,1) AS rp_sum,
       round(avg(e.race_point)::numeric,1) AS rp_avg
FROM keirin.wt_entries e JOIN keirin.wt_races r USING(race_key)
WHERE r.race_date = '2026-06-13' AND substr(e.race_key,10,2) IN ('43','61')
GROUP BY 1,2 ORDER BY 1 LIMIT 6;
