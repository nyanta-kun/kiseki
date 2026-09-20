COPY (
WITH sub AS (
  SELECT DISTINCT ON (netkeirin_race_id)
    netkeirin_race_id, race_key AS sub_race_key, rank_key, origin, is_confident,
    status, published_at, proposed_at, submitted_at, session, title, comment, bet_detail,
    settled_bet, settled_payout, settled_hit, settled_n_combos
  FROM keirin.netkeirin_submissions
  WHERE netkeirin_race_id IS NOT NULL AND status <> 'deleted'
  ORDER BY netkeirin_race_id, (status='published') DESC, submitted_at
)
SELECT r.race_id, r.race_key, r.race_date, r.venue_code, r.race_no, r.race_label,
  r.n_sold, r.sold_points, r.sold_paid_points, r.avg_sold_points,
  r.avg_sold_minutes, r.avg_sold_hour,
  r.stake_amount, r.payout_amount, r.n_hits_incl_garami, r.n_hits_excl_garami,
  w.grade, w.race_type, w.n_entries, w.day_index, w.cup_grade, w.cup_name, w.start_at, w.venue_id,
  s.rank_key, s.origin, s.is_confident, s.status, s.published_at, s.proposed_at, s.submitted_at,
  s.session, s.title, s.settled_bet, s.settled_payout, s.settled_hit, s.settled_n_combos,
  s.bet_detail, length(s.comment) AS comment_len
FROM keirin.netkeirin_sales_race r
LEFT JOIN keirin.wt_races w ON w.race_key = r.race_key
LEFT JOIN sub s ON s.netkeirin_race_id = r.race_id
ORDER BY r.race_date, r.race_id
) TO STDOUT WITH CSV HEADER;
