WITH x AS (
  SELECT r.race_date, e.race_key, r.n_entries, r.grade,
         avg(e.race_point) AS rp_avg
  FROM keirin.wt_entries e JOIN keirin.wt_races r USING(race_key)
  WHERE r.race_date >= '2025-01-01' AND r.race_date <= '2026-09-19'
    AND r.grade IN ('S級','SA混合')
  GROUP BY 1,2,3,4
)
SELECT race_date, count(*) AS suspect_races, round(min(rp_avg)::numeric,1),
       round(max(rp_avg)::numeric,1)
FROM x WHERE rp_avg < 70 GROUP BY 1 ORDER BY 2 DESC LIMIT 25;
