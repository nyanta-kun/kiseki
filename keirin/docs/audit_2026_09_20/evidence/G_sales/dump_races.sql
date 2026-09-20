COPY (
WITH firsth AS (
  SELECT venue_id, race_date, min((int8(start_at)+9*3600) % 86400)/3600.0 AS first_hour
  FROM keirin.wt_races WHERE start_at IS NOT NULL AND start_at <> '' AND race_date >= '2026-08-01'
  GROUP BY 1,2
),
sub AS (
  SELECT DISTINCT ON (netkeirin_race_id)
    netkeirin_race_id, race_key AS sub_race_key, rank_key, origin, is_confident, confident_ev,
    status, published_at, proposed_at, submitted_at, title, comment, bet_detail,
    settled_bet, settled_payout, settled_hit, settled_n_combos, session
  FROM keirin.netkeirin_submissions
  WHERE netkeirin_race_id IS NOT NULL AND status <> 'deleted'
  ORDER BY netkeirin_race_id, (status='published') DESC, submitted_at
),
tl AS (
  SELECT DISTINCT ON (race_key) race_key, type_label, plan_key, bet_type, n_legs, budget,
    pred_mean_payout, pred_min_payout, axis_sum, gap, arare, day_index AS tl_day_index, hit AS tl_hit, payout AS tl_payout
  FROM keirin.type_lab_picks WHERE mode IN ('live','live9') AND race_date >= '2026-08-01'
  ORDER BY race_key, generated_at DESC
)
SELECT r.race_id, r.race_key, r.race_date, r.venue_code, r.race_no, r.race_label,
  r.n_sold, r.sold_points, r.sold_paid_points, r.avg_sold_minutes, r.avg_sold_hour,
  r.stake_amount, r.payout_amount, r.n_hits_incl_garami, r.n_hits_excl_garami,
  w.grade, w.race_type, w.n_entries, w.day_index, w.cup_grade, w.cup_name, w.start_at,
  f.first_hour,
  s.rank_key, s.origin, s.is_confident, s.status, s.published_at, s.title,
  s.settled_bet, s.settled_payout, s.settled_hit, s.settled_n_combos, s.session,
  tl.type_label, tl.plan_key, tl.bet_type, tl.n_legs, tl.budget, tl.pred_mean_payout, tl.axis_sum, tl.gap
FROM keirin.netkeirin_sales_race r
LEFT JOIN keirin.wt_races w ON w.race_key = r.race_key
LEFT JOIN firsth f ON f.venue_id = w.venue_id AND f.race_date = w.race_date
LEFT JOIN sub s ON s.netkeirin_race_id = r.race_id
LEFT JOIN tl ON tl.race_key = r.race_key
ORDER BY r.race_date, r.race_id
) TO STDOUT WITH CSV HEADER;
