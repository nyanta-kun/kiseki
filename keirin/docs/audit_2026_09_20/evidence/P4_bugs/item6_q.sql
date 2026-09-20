WITH last_skip AS (
  SELECT race_key, max(decided_at) AS last_skip_at
  FROM keirin.submission_skips
  WHERE reason_code = 'missing_lineup'
  GROUP BY race_key
),
picks AS (
  SELECT race_key, plan_key, generated_at
  FROM keirin.type_lab_picks
  WHERE mode = 'live'
)
SELECT p.race_key, min(p.generated_at) AS min_gen, max(p.generated_at) AS max_gen,
       ls.last_skip_at,
       (max(p.generated_at) < ls.last_skip_at) AS never_rebuilt_after_last_skip
FROM picks p
JOIN last_skip ls ON ls.race_key = p.race_key
GROUP BY p.race_key, ls.last_skip_at
ORDER BY p.race_key;
