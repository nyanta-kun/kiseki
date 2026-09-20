-- duplicates
SELECT s.netkeirin_race_id, count(*) n, string_agg(s.rank_key||':'||s.status,',' ORDER BY s.rank_key) ranks
FROM keirin.netkeirin_submissions s
WHERE s.netkeirin_race_id IS NOT NULL AND s.race_key>='20260801'
GROUP BY 1 HAVING count(*)>1 ORDER BY 1;
-- submissions not appearing in sales, by status
SELECT s.status, count(*) FROM keirin.netkeirin_submissions s
LEFT JOIN keirin.netkeirin_sales_race r ON r.race_id=s.netkeirin_race_id
WHERE s.race_key>='20260801' AND r.race_id IS NULL GROUP BY 1 ORDER BY 2 DESC;
-- sales rows without submission
SELECT count(*) AS sales_without_sub FROM keirin.netkeirin_sales_race r
LEFT JOIN keirin.netkeirin_submissions s ON r.race_id=s.netkeirin_race_id
WHERE s.race_key IS NULL;
