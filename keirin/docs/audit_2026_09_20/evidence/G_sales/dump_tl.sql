COPY (
  SELECT DISTINCT ON (race_key, plan_key) race_key, plan_key, type_label, bet_type, n_legs, budget,
    pred_mean_payout, pred_min_payout, axis_sum, gap, arare, n_entries AS tl_entries, race_type AS tl_race_type,
    hit AS tl_hit, payout AS tl_payout, final_odds
  FROM keirin.type_lab_picks WHERE race_date >= '2026-08-01'
  ORDER BY race_key, plan_key, generated_at DESC
) TO STDOUT WITH CSV HEADER;
