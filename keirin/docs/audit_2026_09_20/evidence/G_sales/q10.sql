SELECT count(*) AS settled_subs,
       count(*) FILTER (WHERE p.race_key IS NULL) AS no_payout_rows
FROM keirin.netkeirin_submissions s
LEFT JOIN (SELECT DISTINCT race_key FROM keirin.wt_race_payouts WHERE race_key >= '20260816') p ON p.race_key = s.race_key
WHERE s.settled_at IS NOT NULL AND s.race_key >= '20260816' AND s.status <> 'deleted';
SELECT s.race_key, s.rank_key, s.settled_payout FROM keirin.netkeirin_submissions s
LEFT JOIN (SELECT DISTINCT race_key FROM keirin.wt_race_payouts) p ON p.race_key = s.race_key
WHERE s.settled_at IS NOT NULL AND s.race_key >= '20260816' AND s.status <> 'deleted' AND p.race_key IS NULL LIMIT 20;
