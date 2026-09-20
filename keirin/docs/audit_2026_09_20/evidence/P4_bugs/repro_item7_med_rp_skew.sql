-- item7: race_point==0 の穴埋め中央値が「学習(全期間)」と「配信(1日ぶん)」でずれる
SELECT count(*) AS total, count(*) FILTER (WHERE race_point = 0.0) AS zero_rp
FROM keirin.wt_entries WHERE race_point IS NOT NULL;
-- 739,646行中 2,501行 (0.338%) が race_point=0（穴埋め対象）

SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY race_point) AS global_median
FROM keirin.wt_entries WHERE race_point IS NOT NULL AND race_point <> 0.0;
-- 全期間(2022-12-01〜2026-09-20・737,145行)の中央値 = 85.55

-- 単日の中央値（配信時に相当する母集団サイズ）は 82.4〜91.44 で ±3〜7% 振れる
SELECT count(*) n, percentile_cont(0.5) WITHIN GROUP (ORDER BY race_point) AS day_median
FROM keirin.wt_entries
WHERE race_key LIKE '20260610%' AND race_point IS NOT NULL AND race_point <> 0.0;
