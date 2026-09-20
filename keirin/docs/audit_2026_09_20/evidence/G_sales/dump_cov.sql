COPY (
 SELECT w.race_key, w.race_date, w.venue_id, w.race_no, w.race_type, w.n_entries, w.cup_grade, w.grade, w.day_index, w.cancel, w.status,
        (r.race_id IS NOT NULL) AS covered, coalesce(r.sold_paid_points,0) AS paid, coalesce(r.n_sold,0) AS nsold
 FROM keirin.wt_races w
 LEFT JOIN keirin.netkeirin_sales_race r ON r.race_key = w.race_key
 WHERE w.race_date BETWEEN '2026-08-01' AND '2026-09-18'
) TO STDOUT WITH CSV HEADER;
