COPY (
  SELECT race_key, frame_no, finish_order
  FROM keirin.wt_entries
  WHERE race_key IN (SELECT DISTINCT race_key FROM keirin.type_lab_picks
                      WHERE mode='live' AND race_date BETWEEN '2026-08-27' AND '2026-09-19'
                        AND settled_at IS NOT NULL)
) TO STDOUT WITH CSV HEADER
