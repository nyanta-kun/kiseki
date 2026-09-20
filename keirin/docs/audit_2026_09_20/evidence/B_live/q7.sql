\x
SELECT race_key, rank_key, status, origin, is_confident, settled_bet, settled_payout, settled_hit, settled_n_combos, bet_detail
FROM keirin.netkeirin_submissions
WHERE race_key LIKE '202609%' AND status='published' AND settled_hit
ORDER BY race_key LIMIT 2;
