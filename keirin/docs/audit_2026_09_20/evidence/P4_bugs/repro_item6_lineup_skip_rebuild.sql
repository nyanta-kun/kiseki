-- item6: missing_lineup で見送られたレースが、その後 type_lab_picks(mode=live) 側で
-- 実際に組み直されているか（＝退化したまま最終行として残っていないか）
WITH last_skip AS (
  SELECT race_key, max(decided_at) AS last_skip_at
  FROM keirin.submission_skips
  WHERE reason_code = 'missing_lineup'
  GROUP BY race_key
),
picks AS (
  SELECT race_key, plan_key, generated_at
  FROM keirin.type_lab_picks
  WHERE mode = 'live'
)
SELECT p.race_key, min(p.generated_at) AS min_gen, max(p.generated_at) AS max_gen,
       ls.last_skip_at,
       (max(p.generated_at) < ls.last_skip_at) AS never_rebuilt_after_last_skip
FROM picks p
JOIN last_skip ls ON ls.race_key = p.race_key
GROUP BY p.race_key, ls.last_skip_at
ORDER BY p.race_key;
-- 実測: 237レース中 never_rebuilt_after_last_skip=true は7件、いずれも
-- 2026-08-28（型ラボの実売開始=8/29より前）。8/29以降は0件。
-- その7件のうち実際に売られた3件(56_05/56_06/73_05)は origin='rank'/'marquee_fill'
-- ＝旧ランク経路の入稿で、型ラボ側 live 行とは無関係（型ラボはまだ検証中）。

-- paper/paper9 は生成が race_date より最短1日・平均327日遅い
-- （＝生成時点で wt_entries は既にバックフィル済み。当日朝の欠測は構造的に無縁）
SELECT mode, count(*) n,
       min(generated_at::date - race_date) AS min_lag_days,
       max(generated_at::date - race_date) AS max_lag_days,
       avg(generated_at::date - race_date) AS avg_lag_days
FROM keirin.type_lab_picks GROUP BY mode ORDER BY mode;
