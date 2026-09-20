SELECT substr(race_key,1,6) AS ym,
  count(*) n,
  count(published_at) has_pub,
  count(*) FILTER (WHERE published_at IS NOT NULL AND to_char(published_at,'YYYYMMDD') = substr(race_key,1,8)) AS pub_same_day,
  count(title) FILTER (WHERE title<>'') AS has_title,
  count(settled_at) AS settled
FROM keirin.netkeirin_submissions WHERE race_key >= '20260801' GROUP BY 1 ORDER BY 1;
SELECT to_char(published_at,'YYYY-MM-DD') d, count(*) FROM keirin.netkeirin_submissions WHERE race_key>='20260801' AND published_at IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 40;
