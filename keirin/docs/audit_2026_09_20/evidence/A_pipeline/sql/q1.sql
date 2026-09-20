SELECT rank_key, origin, count(*) AS n,
       min(left(race_key,8)) AS d0, max(left(race_key,8)) AS d1,
       count(*) FILTER (WHERE status='published') AS pub,
       count(*) FILTER (WHERE is_confident) AS conf
FROM keirin.netkeirin_submissions
WHERE left(race_key,8) >= '20260901'
GROUP BY 1,2 ORDER BY 3 DESC;
