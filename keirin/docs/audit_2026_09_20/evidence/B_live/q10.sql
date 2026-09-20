SELECT mode, count(*) n, min(race_date) mind, max(race_date) maxd,
       min(generated_at) ming, max(generated_at) maxg,
       count(DISTINCT rule_version) nrv, count(*) FILTER (WHERE settled_at IS NOT NULL) settled
FROM keirin.type_lab_picks GROUP BY 1 ORDER BY 2 DESC;
