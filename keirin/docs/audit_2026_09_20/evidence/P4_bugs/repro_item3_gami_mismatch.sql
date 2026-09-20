-- item3: 2026-08-21 の2レースで netkeirin 帳簿 vs 自社 settled_* が食い違う
SELECT race_key, rank_key, status, settled_bet, settled_payout, settled_hit, bet_detail
FROM keirin.netkeirin_submissions
WHERE race_key IN ('20260821_46_03','20260821_53_04');

SELECT race_key, frame_no, finish_order FROM keirin.wt_entries
WHERE race_key IN ('20260821_46_03','20260821_53_04') ORDER BY race_key, finish_order;

SELECT race_id, race_key, stake_amount, payout_amount, n_hits_incl_garami, n_hits_excl_garami
FROM keirin.netkeirin_sales_race WHERE race_key IN ('20260821_46_03','20260821_53_04');

-- 実際の当たり目(1=2=5)のオッズ・自社の買い目3本のオッズを見比べる
SELECT combination, odds_value FROM keirin.wt_odds
WHERE race_key='20260821_46_03' AND bet_type='trio'
  AND combination IN ('1-2-5','1-2-3','1-2-7','1-2-4');
-- 結論: 1-2-5(3.0倍) は自社が買った3本(1-2-3/1-2-7/1-2-4)のどれとも一致しない
-- ＝現在のDBデータでは「外れ」判定が正しい。netkeirin側の3,600円/8,000円の
-- 出どころは特定できず（判定不能）。再入稿・欠車の形跡なし。
