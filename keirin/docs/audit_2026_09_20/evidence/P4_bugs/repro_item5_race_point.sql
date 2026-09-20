-- item5: 2026-06-12 の race_point 汚染（venue 43/61）
SELECT race_key, count(*) n, sum(race_point) sum_rp, avg(race_point) avg_rp
FROM keirin.wt_entries
WHERE race_key LIKE '20260612_43_%' OR race_key LIKE '20260612_61_%'
GROUP BY race_key ORDER BY race_key;
-- 実測: 24レース・210行、Σrace_point が車数に依らず約300（正常なら車数×90〜100）
