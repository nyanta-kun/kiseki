SELECT settled_hit, count(*) FROM keirin.netkeirin_submissions WHERE settled_at IS NOT NULL GROUP BY 1;
SELECT count(*) AS payout_gt_0 FROM keirin.netkeirin_submissions WHERE settled_at IS NOT NULL AND settled_payout>0;
SELECT count(*) AS payout_gt_bet FROM keirin.netkeirin_submissions WHERE settled_at IS NOT NULL AND settled_payout>settled_bet;
SELECT race_key, rank_key, settled_bet, settled_payout, settled_hit, settled_n_combos, settled_at
FROM keirin.netkeirin_submissions WHERE settled_payout>0 ORDER BY settled_payout DESC LIMIT 5;
