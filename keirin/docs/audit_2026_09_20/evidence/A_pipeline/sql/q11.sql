SELECT substr(e.race_key,10,2) AS venue_code, count(DISTINCT e.race_key) AS races,
       count(*) AS rows,
       count(*) FILTER (WHERE e.player_class LIKE 'S%' AND e.race_point < 30) AS bad,
       round(min(e.race_point)::numeric,1) AS rp_min, round(max(e.race_point)::numeric,1) AS rp_max
FROM keirin.wt_entries e JOIN keirin.wt_races r USING(race_key)
WHERE r.race_date = '2026-06-12' GROUP BY 1 ORDER BY bad DESC;
