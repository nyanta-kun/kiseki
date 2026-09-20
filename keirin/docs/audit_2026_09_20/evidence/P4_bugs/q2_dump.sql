COPY (
  SELECT id, race_key, race_date, plan_key, bet_type, budget, legs, hit, payout, settled_at
  FROM keirin.type_lab_picks
  WHERE mode='live' AND race_date BETWEEN '2026-08-27' AND '2026-09-19'
    AND settled_at IS NOT NULL
) TO STDOUT WITH CSV HEADER
