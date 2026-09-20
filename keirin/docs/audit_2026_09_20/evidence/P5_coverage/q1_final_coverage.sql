-- 決勝レースの定義: race_type に '決勝' を含み '準決勝' を含まない (keirin_marquee.py の MARQUEE_KEYWORDS の一部)
WITH finals AS (
  SELECT race_key, race_date, venue_id, race_no, race_type, n_entries, cup_grade
  FROM keirin.wt_races
  WHERE race_type LIKE '%決勝%' AND race_type NOT LIKE '%準決勝%'
    AND cancel = 0
),
sub AS (
  SELECT race_key,
         bool_or(status <> 'deleted') AS has_product,
         array_agg(DISTINCT rank_key) FILTER (WHERE status <> 'deleted') AS rank_keys,
         array_agg(DISTINCT origin) FILTER (WHERE status <> 'deleted') AS origins
  FROM keirin.netkeirin_submissions
  GROUP BY race_key
)
SELECT
  CASE WHEN f.race_date >= '2026-08-29' THEN 'type_lab_era' ELSE 'pre_type_lab' END AS era,
  count(*) AS n_finals,
  count(*) FILTER (WHERE s.has_product) AS n_with_product,
  count(*) FILTER (WHERE s.has_product IS NULL OR NOT s.has_product) AS n_no_product
FROM finals f
LEFT JOIN sub s ON s.race_key = f.race_key
GROUP BY 1
ORDER BY 1;
