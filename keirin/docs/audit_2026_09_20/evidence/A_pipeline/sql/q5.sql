SELECT mode, plan_key, count(*) n, count(DISTINCT race_key) r
FROM keirin.type_lab_picks WHERE race_date = '2026-09-20'
GROUP BY 1,2 ORDER BY 1,2;
SELECT mode, type_label, count(DISTINCT race_key) FROM keirin.type_lab_picks
WHERE race_date='2026-09-20' GROUP BY 1,2 ORDER BY 1,2;
SELECT reason_code, count(*) FROM keirin.submission_skips
WHERE race_date='2026-09-20' GROUP BY 1 ORDER BY 2 DESC;
