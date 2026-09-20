SELECT bet_type, count(*) n, count(DISTINCT race_key) races,
       min(substring(race_key,1,8)) mind, max(substring(race_key,1,8)) maxd
FROM keirin.wt_race_payouts GROUP BY 1 ORDER BY 2 DESC;
