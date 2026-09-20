-- 同一 (cup_id, player_id, day_index) に2レース以上ある行の件数（2026年のみ）
WITH x AS (
  SELECT r.cup_id, e.player_id, r.day_index, count(*) AS n
  FROM keirin.wt_entries e JOIN keirin.wt_races r ON e.race_key = r.race_key
  WHERE r.race_date >= '2026-01-01' AND r.race_date < '2026-09-01'
  GROUP BY 1,2,3
)
SELECT n AS races_same_day, count(*) AS groups, sum(n) AS rows
FROM x GROUP BY 1 ORDER BY 1;
