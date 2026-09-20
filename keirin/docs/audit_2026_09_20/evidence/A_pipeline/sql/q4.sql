SELECT left(race_key,8) d, status, origin, count(*) FROM keirin.netkeirin_submissions
WHERE left(race_key,8) >= '20260915' GROUP BY 1,2,3 ORDER BY 1 DESC,2,3;
