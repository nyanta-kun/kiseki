SELECT rank_key, count(*), min(title), max(title) FROM keirin.netkeirin_submissions
WHERE race_key>='20260901' AND title IS NOT NULL AND title<>'' GROUP BY 1 ORDER BY 2 DESC LIMIT 25;
