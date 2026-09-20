SELECT race_key, rank_key, substr(bet_detail,1,400) AS bet, settled_fp, settled_bet, settled_payout, settled_n_combos
FROM keirin.netkeirin_submissions WHERE race_key IN ('20260821_46_03','20260821_53_04');
