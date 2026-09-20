-- item4: 8/09-8/15 に status IN (submitted,published) なのに netkeirin_sales_race に無い29件
SELECT ns.race_key, ns.rank_key, ns.status, ns.origin, ns.submitted_at, ns.published_at,
       ns.netkeirin_race_id
FROM keirin.netkeirin_submissions ns
WHERE ns.status IN ('published','submitted')
  AND ns.race_key BETWEEN '20260809' AND '20260816'
  AND NOT EXISTS (SELECT 1 FROM keirin.netkeirin_sales_race sr WHERE sr.race_key = ns.race_key)
ORDER BY ns.race_key;
-- 実測: 29件全部 status='submitted' かつ published_at IS NULL（'published' は
-- 2026-08-16 導入前で存在しない状態遷移）。netkeirin_race_id で
-- netkeirin_sales_race.race_id を引いても一致するレコードが1件も無い
-- （race_id が別キーで存在するわけでもない＝完全に不在）。
-- 同じ期間、sales_race は日あたり20-48件の実データを持つ（丸ごと欠測ではない）。
-- ＝ 個別の「作成はしたが公開まで到達しなかった」入稿である可能性が高い
--   （B_live/REPORT.md の仮説と一致・追加の反証は見つからず）。
