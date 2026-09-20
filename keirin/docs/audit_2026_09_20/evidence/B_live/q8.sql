SELECT substring(race_key,1,8) d, count(*) subs FROM keirin.netkeirin_submissions
WHERE deleted_at IS NULL AND status IN ('published','submitted')
  AND race_key BETWEEN '20260805' AND '20260817'
GROUP BY 1 ORDER BY 1;
