WITH fin AS (
  SELECT w.race_key, w.n_entries FROM keirin.wt_races w
  LEFT JOIN keirin.netkeirin_sales_race r ON r.race_key=w.race_key
  WHERE w.race_date BETWEEN '2026-08-29' AND '2026-09-18' AND w.cancel=0
    AND w.race_type LIKE '%決勝%' AND w.race_type NOT LIKE '%準決勝%' AND r.race_id IS NULL
)
SELECT (SELECT count(*) FROM fin) AS uncovered_finals,
       (SELECT count(DISTINCT race_key) FROM keirin.submission_skips s JOIN fin USING (race_key)) AS with_skip_rows;
SELECT s.reason_code, count(DISTINCT s.race_key) FROM keirin.submission_skips s
JOIN (SELECT w.race_key FROM keirin.wt_races w
  LEFT JOIN keirin.netkeirin_sales_race r ON r.race_key=w.race_key
  WHERE w.race_date BETWEEN '2026-08-29' AND '2026-09-18' AND w.cancel=0
    AND w.race_type LIKE '%決勝%' AND w.race_type NOT LIKE '%準決勝%' AND r.race_id IS NULL) f USING (race_key)
GROUP BY 1 ORDER BY 2 DESC;
