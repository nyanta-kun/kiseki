SELECT race_key, venue_id, race_date, race_no, grade, race_type, start_at, n_entries, day_index, cup_grade, cup_name FROM keirin.wt_races WHERE race_date='2026-09-18' LIMIT 3;
SELECT count(*) AS n, count(w.race_key) AS matched FROM keirin.netkeirin_sales_race r LEFT JOIN keirin.wt_races w ON w.race_key = r.race_key;
