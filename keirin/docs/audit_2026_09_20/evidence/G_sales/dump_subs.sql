COPY (SELECT race_key, rank_key, origin, status, is_confident, confident_ev, session,
 submitted_at, proposed_at, published_at, settled_bet, settled_payout, settled_hit, settled_n_combos, settled_at,
 netkeirin_race_id, title
 FROM keirin.netkeirin_submissions WHERE race_key >= '20260801' ORDER BY race_key, rank_key) TO STDOUT WITH CSV HEADER;
