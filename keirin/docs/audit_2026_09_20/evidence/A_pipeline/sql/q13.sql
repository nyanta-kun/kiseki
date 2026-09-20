-- 「race_point が確率%で上書きされた」署名: レース内平均が 60 未満（実得点は 90〜100 前後）
WITH x AS (
  SELECT r.race_date, e.race_key, r.n_entries,
         avg(e.race_point) AS rp_avg, sum(e.race_point) AS rp_sum
  FROM keirin.wt_entries e JOIN keirin.wt_races r USING(race_key)
  WHERE r.race_date >= '2026-01-01' AND r.race_date <= '2026-09-19'
  GROUP BY 1,2,3
)
SELECT race_date, count(*) AS suspect_races,
       round(min(rp_avg)::numeric,1) AS min_avg, round(max(rp_avg)::numeric,1) AS max_avg
FROM x WHERE rp_avg < 60 GROUP BY 1 ORDER BY 1;
