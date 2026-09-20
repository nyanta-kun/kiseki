\pset format unaligned
SELECT p.race_key, p.plan_key, p.type_label, p.axis_sum, p.arare, p.gap, p.n_legs,
       p.pred_mean_payout, p.pred_min_payout, p.rule_version, p.hit, p.payout, p.win_combo,
       p.legs
FROM keirin.type_lab_picks p
JOIN keirin.netkeirin_submissions s USING (race_key)
WHERE p.race_date='2026-09-19' AND s.rank_key=p.plan_key AND p.mode='live' AND p.hit IS TRUE
ORDER BY p.payout DESC LIMIT 1;
