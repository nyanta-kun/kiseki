SELECT (SELECT count(*) FROM keirin.netkeirin_sales_race) AS sales,
 (SELECT count(*) FROM keirin.netkeirin_submissions WHERE race_key >= '20260801') AS subs_all,
 (SELECT count(*) FROM keirin.netkeirin_submissions s JOIN keirin.netkeirin_sales_race r ON r.race_id=s.netkeirin_race_id) AS joined_by_id,
 (SELECT count(*) FROM keirin.netkeirin_submissions s JOIN keirin.netkeirin_sales_race r ON r.race_key=s.race_key) AS joined_by_key;
SELECT status, count(*) FROM keirin.netkeirin_submissions WHERE race_key >= '20260801' GROUP BY 1 ORDER BY 2 DESC;
SELECT count(*) AS dup_race_ids FROM (SELECT netkeirin_race_id FROM keirin.netkeirin_submissions WHERE netkeirin_race_id IS NOT NULL AND race_key>='20260801' GROUP BY 1 HAVING count(*)>1) t;
SELECT count(*) AS sold_points_ne_300xn FROM keirin.netkeirin_sales_race WHERE sold_points <> n_sold*300;
