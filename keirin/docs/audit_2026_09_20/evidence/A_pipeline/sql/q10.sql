SELECT r.race_date, count(*) FILTER (WHERE e.player_class LIKE 'S%' AND e.race_point < 30) AS bad,
       count(*) AS rows
FROM keirin.wt_entries e JOIN keirin.wt_races r USING(race_key)
WHERE r.race_date >= '2026-06-01' AND r.race_date <= '2026-07-31'
GROUP BY 1 HAVING count(*) FILTER (WHERE e.player_class LIKE 'S%' AND e.race_point < 30) > 0
ORDER BY 1;
