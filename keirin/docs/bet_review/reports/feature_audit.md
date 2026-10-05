# 特徴量 取得タイミング監査（既存証拠への索引 + 穴の特定）

作成: 2026-10-05 / 対象ブランチ: feat/keirin-bet-review / コード変更なし・DB 書き込みなし。
方針: 2026-09-20 監査（`keirin/docs/AUDIT_2026_09_20.md`）を作り直さず、列ごとに「既存証拠が取得時点を検証済みか」を引く。採否・改善提案は書かない。

## 結論3行

1. 本番の p3 モデル `lgbm_wt_eval` と pw モデル `lgbm_wt_win` はどちらも `FEATURE_COLS_WT` の 70 列を、同じ順序で使っている（pkl の `feature_name_` を直接読んで一致を確認）。70 列のうちレース結果・確定オッズ・自社予測値（`finish_order`/`factor`/`res_*`/`final_half`/`pred_*_pct`/`wt_odds`）を直接の入力にする列は 0 本。
2. 70 列の内訳は、既存証拠で取得時点を検証済み 33 列／「朝 07:00 時点で未確定になりうる」ことが実証済みの並び・AI 印系 20 列／取得時点を検証した記述が無い 17 列。「未検証」は 17 列（+ 実証済み skew の 20 列は朝値と最終値の直接比較が未実施）。
3. 発走後の値が学習に混ざっていると実測で示せた列は無い。示せたのは次の 3 件: (a) 並び・AI 印は朝に欠測し学習時は必ず充足（234R/3,912R = 5.98%）、(b) `race_point` に 2026-06-12 の破損 210 行と当日暫定値の混入対策、(c) 欠車で出走表の頭数が朝と最終で異なりうる。予測オッズモデル `odds_tf_n7/n9` の入力は p3/pw（上記 70 列由来）+ 出走表 11 列。

## 0. 範囲と読み方

- 呼び出し元の確認: `scripts/build_type_lab_picks.py:330-364 predict_p3_pw` が `build_day_features`（同 :246-273）→ `build_features_wt(load_raw_data_wt(day))` → `prepare_X` を通し、`load_model("lgbm_wt_eval")` / `load_model("lgbm_wt_win")` で予測する。`load_model` は `lgbm_wt*` 系の列名を `FEATURE_COLS_WT` と照合して不一致なら落とす（`src/models/trainer.py:196-250`）。
- 実物確認（`keirin/.venv` で pkl を読んだ結果）: `lgbm_wt_eval` 70 列・`lgbm_wt_win` 70 列とも `list(feature_name_) == FEATURE_COLS_WT` が True。`data/models/lgbm_wt_eval.meta.json`: `feature_count 70`・`full_refit false`・`test_from 2026-06-22`・`trained_at 2026-09-21T00:07:19`。`lgbm_wt_win.meta.json`: `feature_count 70`・`full_refit true`・`trained_at 2026-09-21T02:12:41`。
- 予測オッズ `odds_tf_n7/n9`: `lightgbm.Booster` の特徴名 63 個が `src/odds_prediction_tf.py:FEATURE_NAMES`・`odds_tf_meta.json` と一致（7 車・9 車とも）。`train_end 2025-12-31`（`n_train_races` 7車 7,145 / 9車 3,536）。
- 実行時刻（`AUDIT_2026_09_20.md` §2.1・`scripts/type_lab_daily.sh`）: 07:00 `daily_picks_wt.sh` の `collect-wt`（`daily_picks_wt.sh:145`）→ 07:1x 型ラボ生成 → 07:31 入稿。13:05 / 18:05 `type_lab_wave.sh` は朝に並び・印が欠測だったレースを拾い直す。前日結果の再収集は 06:30 `previous_day_wt.sh`（`daily_picks_wt.sh` 末尾に未実施時の保険あり）。
- 本リポジトリ内の `keirin/data/keirin.db`（SQLite）は `wt_entries` が 0 行・`wt_races` が 8 行（2026-08-19 のみ）で、列ごとの DB 実測には使えなかった。DB 実測は既存 evidence の記載に依る（VPS PostgreSQL・本タスクでは未接続）。

### 取得タイミングの区分（表で使う記号）

| 記号 | 意味 | 根拠 |
|---|---|---|
| P0 | 静的マスタ／前日以前に確定 | `venue_info`・選手マスタ |
| P1 | 出走表（winticket racecard の `records`/`entries`/`players`）。`collect-wt` 時点の値。結果確定（`finish_order>=1`）まで再収集のたびに `INSERT OR REPLACE` で全列置換 | `src/scraper/pipeline_wt.py:168-183`（スキップ条件）・:193-241（置換） |
| P2 | 並び予想（`linePrediction`）・AI 印（`predictionMark`）。朝 07:00 時点で未公開のことがあり、その場合 13:05/18:05 に拾い直す | `src/scraper/winticket.py:278-281,314,334-338`・`scripts/type_lab_wave.sh`・`src/entry_health.py:66` |
| P3 | 過去（前日以前）の確定結果から作るローリング／節内集計 | 各 `add_*_features_wt` |
| D | 上の列からのレース内派生。入力の区分を引き継ぐ | `build_features_wt` |

検証状況の記号: A = 既存で検証済み / B = 朝に未確定になりうることが実証済み（朝値と最終値の直接比較は未実施） / C = 未検証（該当する検証記述が evidence に無い）。

## 1. 列表（FEATURE_COLS_WT 70 列）

`feature_wt.py` は `src/preprocessing/feature_wt.py`。evidence は `keirin/docs/audit_2026_09_20/evidence/` 以下で、LEAK = `A_pipeline/sub_leak/REPORT_leak.md`、C = `C_model/REPORT.md`。

| # | 列名 | 算出元（テーブル/関数） | 区分 | 根拠（file:line） | 検証 | 既存検証の所在 |
|---|---|---|---|---|---|---|
| 1 | race_point | `wt_entries.race_point`（racePoint）。0.0 は NaN 扱いで固定値 85.55 補完 | P1 | winticket.py:312, feature_wt.py:190-194 | A（注意あり） | LEAK §2.2（節内変化率 0.0000・2026-08 は平均絶対差 0）／C §4（連日 0.0034, n=214,362）。破損: LEAK §2.4・AUDIT §8 #5 |
| 2 | gear_ratio | `wt_entries.gear_ratio`（gearRatio）。NaN→3.92 | P1 | winticket.py:310, feature_wt.py:196 | C | 同じ `records` 由来だが列単体の節内不変性テストは無い |
| 3 | first_rate_norm | `wt_entries.first_rate`/100 | P1 | winticket.py:326, feature_wt.py:181 | A | LEAK §2.2（0.0000）／C §4（0.0000） |
| 4 | third_rate_norm | `wt_entries.third_rate`/100 | P1 | winticket.py:328, feature_wt.py:183 | A | 同上 |
| 5 | style_enc | `wt_entries.style` を逃0/両1/追2 に変換 | P1 | winticket.py:311, feature_wt.py:199 | A | LEAK §2.2（style 0.0000, 2026-06 n=10,248） |
| 6 | player_class_enc | `wt_entries.player_class`（playerCurrentTermClass/Group） | P1 | winticket.py:297-300, feature_wt.py:202 | C | 検証記述なし |
| 7 | frame_no | `wt_entries.frame_no`（entries.number） | P1 | winticket.py:292, feature_wt.py:213 | C | 検証記述なし |
| 8 | score_rank | race_point のレース内順位 | D(1) | feature_wt.py:236 | A | race_point に準ずる |
| 9 | score_z | race_point のレース内 z（±5 clip） | D(1) | feature_wt.py:237-239 | A | 同上 |
| 10 | wr_rank | first_rate_norm のレース内順位 | D(3) | feature_wt.py:242 | A | first_rate に準ずる |
| 11 | top3r_rank | third_rate_norm のレース内順位 | D(4) | feature_wt.py:245 | A | third_rate に準ずる |
| 12 | is_inner | frame_no<=3 | D(7) | feature_wt.py:213 | C | frame_no に準ずる |
| 13 | is_outer | frame_no>=7 | D(7) | feature_wt.py:214 | C | 同上 |
| 14 | bank_length_enc | `venue_info.bank_length`/100（NaN→400） | P0 | feature_wt.py:62-135(JOIN), :226-231 | C | 静的マスタ。検証記述なし |
| 15 | is_indoor | `venue_info.is_indoor` | P0 | feature_wt.py:228-229 | C | 同上 |
| 16 | grade_enc | `wt_races.grade`（級班 S級/A級/L級/SA混合） | P1 | feature_wt.py:208-210, pipeline_wt.py:200-210 | C | 検証記述なし |
| 17 | period_norm | `wt_entries.term`/100（NaN→df 中央値） | P1 | winticket.py:309, feature_wt.py:204-205 | C | 検証記述なし。`med_term` は呼び出し単位の中央値のまま（#2 の固定化は race_point のみ） |
| 18 | is_home | `wt_entries.prefecture` と `venue_info.prefecture` の一致 | P1/P0 | winticket.py:307, feature_wt.py:216-224 | C | 検証記述なし |
| 19 | line_size | `wt_entries.line_size`（linePrediction）。欠測時 1 | P2 | winticket.py:146,335, feature_wt.py:263 | B | LEAK §2.3 |
| 20 | line_pos | `wt_entries.line_pos`。欠測時 1 | P2 | winticket.py:336, feature_wt.py:264 | B | LEAK §2.3 |
| 21 | is_line_leader | `wt_entries.is_line_leader`。欠測時 True(1) | P2 | winticket.py:337, feature_wt.py:265 | B | LEAK §2.3 |
| 22 | line_leader_rp | 所属ライン先頭の race_point | D(1,19-21) | feature_wt.py:517 | B | 入力は #1(A) と P2 |
| 23 | line_leader_rp_gap_top | 最強先頭との得点差 | D | feature_wt.py:526 | B | 同上 |
| 24 | line_leader_rp_rank | 先頭同士の得点順位 | D | feature_wt.py:541 | B | 同上 |
| 25 | line_leader_is_weakest | 所属ラインの先頭が最弱か | D | feature_wt.py:545 | B | 同上 |
| 26 | line_rp_spread | ライン内の得点 max−min | D | feature_wt.py:552 | B | 同上 |
| 27 | line_rp_lead_minus_deputy | 先頭−番手の得点差 | D | feature_wt.py:563 | B | 同上 |
| 28 | n_lines | `wt_entries.n_lines`（linePrediction の本数）。欠測時 0 | P2 | winticket.py:281,338, feature_wt.py:266 | B | LEAK §2.3 |
| 29 | is_isolated | line_size==1 | D(19) | feature_wt.py:267 | B | 同上 |
| 30 | line_frac | line_size / レース内行数 | D(19) | feature_wt.py:270-271 | B | 同上（分母は欠車除外後の行数。§3.3） |
| 31 | n_senko | レース内の style_enc==0 の人数 | D(5) | feature_wt.py:275 | A | style に準ずる |
| 32 | s_count | `wt_entries.s_count`（standing） | P1 | winticket.py:315, feature_wt.py:251 | A | LEAK §2.2（0.0000）／C §4（0.0000） |
| 33 | h_count | `wt_entries.h_count`（home） | P1 | winticket.py:316, feature_wt.py:252 | A | LEAK §2.2（h_count 0.0006） |
| 34 | b_count | `wt_entries.b_count`（back） | P1 | winticket.py:317, feature_wt.py:253 | A | LEAK §2.2（決定実験 9,774 ペアで delta=0）／C §4（0.0000） |
| 35 | prediction_mark | `wt_entries.prediction_mark`（winticket AI 印・0=なし） | P2 | winticket.py:314, feature_wt.py:248 | B | LEAK §2.3。C §4 は split 重要度 24 位（258）と記載。一方 `scripts/snapshot_morning_entries_wt.py` の docstring は「gain 1位の入力」と記載（食い違い・未解決） |
| 36 | win_3m | 過去 90 日の勝率 | P3 | feature_wt.py:658-743（`closed="left"` は :699） | A | LEAK §1.1-1.3 |
| 37 | top3_3m | 同 3着内率 | P3 | 同上 | A | 同上 |
| 38 | quin_3m | 同 連対率 | P3 | 同上 | A | 同上 |
| 39 | win_6m | 過去 180 日の勝率 | P3 | 同上 | A | 同上 |
| 40 | top3_6m | 同 3着内率 | P3 | 同上 | A | 同上 |
| 41 | quin_6m | 同 連対率 | P3 | 同上 | A | 同上 |
| 42 | venue_wr | 選手×場の過去勝率（`expanding().mean().shift(1)`） | P3 | 同上 | A | LEAK §1.3-1.4（同日 2 走 0 件） |
| 43 | days_since | 前走（確定のみ）からの日数 | P3 | 同上 | A | 同上 |
| 44 | wr_trend | win_3m − win_6m | P3 | 同上 | A | 同上 |
| 45 | rp_prev_delta | 今回得点 − 前回出走日の得点 | P3 | feature_wt.py:751-836（`closed="left"` は :818） | A | LEAK §1.1 |
| 46 | rp_delta_90 | 今回得点 − 過去 90 日平均（`closed="left"`） | P3 | 同上 | A | 同上 |
| 47 | rp_delta_180 | 同 180 日 | P3 | 同上 | A | 同上 |
| 48 | rp_trend | 90 日平均 − 180 日平均 | P3 | 同上 | A | 同上 |
| 49 | b_rate_90 | 過去 90 日の B 取得率（`wt_entries.res_back`） | P3 | feature_wt.py:844-965（`closed="left"` は :951） | A | LEAK §1.1。2026-07-28 修正済みバグ（未確定行を drop）は同関数 docstring |
| 50 | s_rate_90 | 同 S 取得率（`res_standing`） | P3 | 同上 | A | 同上 |
| 51 | fh_rel_90 | 同 上がり相対値（`final_half`） | P3 | 同上 | A | 同上 |
| 52 | fh_best_rate_90 | 同 上がり最速率 | P3 | 同上 | A | 同上 |
| 53 | formation_pos_frac | b_rate_90 順の隊列推定位置 | D(49,19-21) | feature_wt.py:573-649 | B | 入力 b_rate_90(A) と lineup(P2) |
| 54 | formation_line_rank | 同 ライン順位 | D | 同上 | B | 同上 |
| 55 | rt_is_final | `wt_races.race_type` に「決勝」を含み「準決」を含まない | P1 | feature_wt.py:395-425 | C | 検証記述なし |
| 56 | rt_is_semifinal | 「準決」を含む | P1 | 同上 | C | 同上 |
| 57 | rt_is_heat | 「予選」を含む | P1 | 同上 | C | 同上 |
| 58 | rt_is_senbatsu | 「選抜」を含む | P1 | 同上 | C | 同上 |
| 59 | rt_is_tokusen | 「特選」を含む | P1 | 同上 | C | 同上 |
| 60 | rt_is_hatsu | 先頭が「初」 | P1 | 同上 | C | 同上 |
| 61 | rt_is_ippan | 「一般」を含む | P1 | 同上 | C | 同上 |
| 62 | line_rp_sum | ライン内 race_point 合計 | D(1,19-21) | feature_wt.py:428-469 | B | 入力 race_point(A) と line_group(P2) |
| 63 | line_rp_max | 同 最大 | D | 同上 | B | 同上 |
| 64 | line_rp_mean | 同 平均 | D | 同上 | B | 同上 |
| 65 | line_rank_by_rp | ライン強さ順位 | D | 同上 | B | 同上 |
| 66 | line_rp_gap_top | 最強ラインとの得点合計差 | D | 同上 | B | 同上 |
| 67 | cup_n_so_far | 今節ここまでの出走数（`shift().expanding().count()`） | P3 | feature_wt.py:972-1056 | A | LEAK §1.1（コード）・§1.3（同日 2 走 0 件） |
| 68 | cup_top3_rate | 今節ここまでの 3着内率 | P3 | 同上 | A | 同上 |
| 69 | cup_win_rate | 今節ここまでの勝率 | P3 | 同上 | A | 同上 |
| 70 | cup_mean_order_n | 今節ここまでの平均着順（頭数正規化） | P3 | 同上 | A | 同上 |

件数: A = 33（#1,3,4,5,8-11,31-34,36-52,67-70。#8-11・#31 は入力が A の派生）、B = 20（#19-30,35,53,54,62-66）、C = 17（#2,6,7,12-18,55-61）。合計 70。

`FEATURE_COLS_WT` に入っていない `wt_entries` 列: `finish_order`/`factor`/`res_standing`/`res_back`/`final_half`（結果系・ターゲットか過去履歴の素材としてのみ使用）、`pred_win_pct`/`pred_top2_pct`/`pred_top3_pct`（自社予測。`feature_wt.py` に該当文字列 0 件: C §4）、`ex_spurt_pct`/`ex_thrust_pct`（2026-07-31 に除外: `feature_wt.py:1285-1308`）、`second_rate`/`ex_left_behind_pct` ほか。

## 2. 予測オッズ `odds_tf_n7/n9` の入力（63 特徴）

p3/pw は上の 70 列モデルの出力。それ以外の入力は出走表の素値 11 列（`src/odds_prediction_tf.py:130-140`、学習側 `scripts/train_odds_prediction_tf.py:78-101`、配信側 `src/odds_prediction.py:load_race_inputs`）。

| 入力 | 使う特徴（FEATURE_NAMES） | 区分 | 検証 |
|---|---|---|---|
| p3（3着内率） | p3_1-3, p3sum, rk_*, lp_prod, ent_p3, p3_max, p3_sum2 | 70 列モデル出力（学習=walk-forward 予測、配信=本番モデル） | 分布差は LEAK §3 が「未計測」と記載 |
| pw（1着率） | pw_1-3, pwsum, rw_*, lp_pl, ent_pw, pw_max, pw_gap12 | 同上 | 同上 |
| race_point | rp_1-3, rp_sum, rp_rel, rp_mean, rp_std, rp_max, rp_gap12/23, rp_range | P1 | A（#1 と同じ） |
| prediction_mark | mk_1-3, n_marked | P2 | B |
| line_group / line_size / line_pos / is_line_leader | same_line_*, n_line_in, line_order_*, lead_at_1, solo_at_1, lpos_1, has_top_line, solo_in, lead_in, n_lines, n_solo, max_line | P2 | B |
| player_class（`CLASS_CODE` 別写像） | cls_sum | P1 | C |
| style | sty_1, sty_lead | P1 | A |
| first_rate / third_rate（素値） | fr_1, fr_sum, tr_sum | P1 | A |
| second_rate（素値） | sr_sum | P1 | LEAK §2.2 の結論文に名前はあるが、同節の表に second_rate の実測行は無い（C §4 の表にも無い） |
| frame_no | frame_1, frame_sum | P1 | C |
| 学習ターゲット | 最終三連単オッズ（`ORDER BY collected_at DESC`） | 確定後（目的変数） | LEAK §3: 設計どおり。入力には使わない |

配信側の p3/pw の出所が 2 系統ある（事実）: `build_type_lab_picks.py:423-424` の `predict_board` はメモリ上の `lgbm_wt_eval`/`lgbm_wt_win` の出力を渡す。`netkeirin_submit_wt.py` 経由の `predicted_trifecta_board` は `wt_entries.pred_top3_pct/pred_win_pct`（`src/odds_prediction.py:load_race_inputs`）を読み、この列は入稿後に `backfill_index_pct_wt.py` が月次 vintage モデルで上書きする（`src/type_lab.py:742-747` の記述）。

## 3. 未検証列とリーク疑い

### 3.1 未検証 17 列（区分 C）

| 列 | 入力の出所 | 本調査で言えること |
|---|---|---|
| gear_ratio | `records`（winticket.py:310） | race_point・rates・s/h/b_count と同じ `records` オブジェクト由来で、それらは節内不変が実測済み。gear_ratio 自体の節内不変性を測った記述は無い |
| player_class_enc | `entries.playerCurrentTermClass/Group`（winticket.py:297-300） | 検証記述なし。`scripts/snapshot_morning_entries_wt.py:SNAP_COLS` には含まれ、朝値との比較が可能な設計 |
| frame_no, is_inner, is_outer | `entries.number` | 検証記述なし |
| bank_length_enc, is_indoor | `venue_info`（静的マスタ） | 検証記述なし。時点で変わる値として扱われている箇所はコード上に無い |
| grade_enc | `wt_races.grade` | 検証記述なし |
| period_norm | `players.term` | 検証記述なし |
| is_home | `players.prefecture` / `venue_info.prefecture` | 検証記述なし |
| rt_is_*（7 列） | `wt_races.race_type` | 検証記述なし |

これら 17 列について、発走後に更新される根拠（コード・DB 実測）は見つかっていない。したがって「リーク疑い」には入れず「未検証」とする。検証手段は存在する: 朝 07:00 の出走表の退避（`scripts/snapshot_morning_entries_wt.py`、2026-09-30 追加・PR #648・保存先 `data/snapshots/wt_entries_morning/YYYY-MM-DD.csv.gz`）を最終値と突き合わせる `--report`。ただしこのリポジトリ作業ツリーに `data/snapshots/` は無く、突き合わせ結果を記録した文書も見つからなかった。

### 3.2 実証済みの skew（区分 B・20 列）

- 事実（LEAK §2.3）: 2026-08-01 以降 `keirin.submission_skips` の `missing_lineup` は 282 件（234 レース）。その 234 レースの現在の `wt_entries` は `n_lines=0` の割合 0.0000・印なし率 0.4275 で、正常日（印なし率 0.42-0.45）と区別がつかない。同期間の総レース 3,912R に対し 5.98%。
- 意味: 朝に退化値（全員 line_size=1・n_lines=0・mark=0）だった行が、学習時には後日充足した値で入っている。発走後ではなく「07:00 以降・発走前」に公開される値。朝値と最終値が、欠測ではないレースでも一致するかは未測定（LEAK §7-1）。
- ガード: 入稿側 `scripts/netkeirin_submit_wt.py`（`src/entry_health.py:66 missing_market_inputs`）と、2026-09-20 に追加された生成側 `scripts/build_type_lab_picks.py:407-419 _lineup_issue`（AUDIT §8 #6）。生成側に入る前の実害は「型ラボ移行後、退化入力のまま売った例 0 件」（AUDIT §8 #6）。

### 3.3 その他、コード上の根拠がある時点差（リーク疑いの定義に当たるかは読み手の判断）

1. race_point の当日暫定値: `scripts/daily_picks_wt.sh:147-153` が「WINTICKET 側がその日の race_point をまだ確定しておらず異常な暫定値（平均 62-67 → 4.33）を収集する事象」を認めている。対策は `scripts/check_race_point_sanity.py`（直近 7 日中央値の 50% 未満なら 5 分待って再収集を最大 3 回、解消しなければその日の指数算出をスキップ）。ガードの網羅率は未測定。
2. race_point の学習データ破損: 2026-06-12 の 24R・210 行（全体の 0.03%）。S 級で得点<30 の割合は 2026-06 が 2.294%、他月は 0.156-0.259%（LEAK §2.4）。DB は書き換えず検知のみ（AUDIT §8 #5・2026-09-20 ユーザー判断）。学習データに残っている。`rp_trend` 系 #45-48 と `score_*`/`line_*` 派生列の元になる。
3. 欠車による出走表の頭数差: 欠車は `winticket.py:289` で除外され、`pipeline_wt.py:246-251` が出走表から消えた枠を `wt_entries` から削除する。よって学習時の `wt_entries` は最終的な出走表、配信時（07:00）は当時の出走表。影響を受ける列: #8-11, #22-30, #31, #62-66（レース内順位・行数・ライン集計を使う列）。規模の根拠は AUDIT §8 #2 の「欠車返還 24 日で 25,400 円（投資の 0.04%）」のみで、列への影響は未測定。
4. `period_norm` の欠損補完: `med_term = df["term"].median()`（feature_wt.py:204-205）は呼び出しごとの中央値。同型の `race_point` は 2026-09-20 に固定値化された（feature_wt.py:190-194）が、`term` は対象外。該当行数は未測定。
5. FEATURE_COLS_WT 外だが本番の型判定が使う列: `ex_left_behind_pct`。`feature_wt.py:1285-1308` は「開催中に更新される」と明記（21.4%）、`src/type_lab.py:74-88` も同節連続日で 15.00% 変化・閾値 `BEHIND_MID=11.0` を跨ぐのはライン先頭で 1.94% と記載。p3/pw/予測オッズの入力ではなく、`type_lab.py:706` の型（arare）判定の入力。

### 3.4 リーク疑いの有無

- 発走前に得られない値（確定オッズ・レース結果・レース後に更新される得点や級班）を直接入力にしている列: 本調査では 0 列。根拠: (i) 70 列と 63 列の特徴名を実モデルで確認、(ii) ローリング／節内集計はすべて `closed="left"` または `shift()` で当日を除外（LEAK §1.1-1.3、同日 2 走 0 件・2026 年 141,017 選手日）、(iii) `race_point`/`first_rate`/`third_rate`/`s_count`/`b_count` は同一節の連日で変化率 0.0000（LEAK §2.2・C §4、n=214,362）。
- 上の根拠が及ばない列は 3.1 の 17 列。これらは疑う根拠もないが、安全だと示す証拠もない。

## 4. 既存証拠への索引

### 4.1 検証方法（分割・期間・評価指標）

出典: `keirin/docs/AUDIT_2026_09_20.md` §3 と `evidence/C_model/REPORT.md`、`keirin/docs/vintage_model_policy.md`。

- honest walk-forward: 四半期 expanding・9 窓・2024-07-01〜2026-08-31（422,813 行・59,650 レース）。各窓の学習は 2022-12-01〜窓開始前日。本番と同じハイパーパラメータ（500 木・lr 0.05・leaves 31・colsample 0.8・seed 42、`src/models/trainer.py:63-81`）・同じ母集団（DNF を負例に含む）。既存 WF キャッシュ（60 特徴）との相関 p3 0.9835 / pw 0.9817（C §1）。
- 指標: AUC 3着内 0.7824・AUC 1着 0.8343（全体）。窓別 0.778-0.790 / 0.829-0.848。pw 1位の1着率 0.4722・上位2車そろい 0.5322。較正は十分位で最大 1.8pt（C §2）。型境界 `AXIS_SUM_FIRM=1.44` は堅い 64.96% ↔ 混戦 39.76%（7 車 50,498R）。
- 市場比較: 無作為 4,160 レース（7 車・全員完走）、walk-forward 条件付きロジット 7 窓、bootstrap 2,000 回。市場に対するモデルの追加情報 Δlogloss は 1着 −0.00013 [−0.00039, +0.00015]・3着内 −0.00006 [−0.00052, +0.00046]（C §3）。
- 前向き: 朝に保存された軸、2026-08-27〜09-20、7 車 1,615R。axis1 の1着率 46.32% [43.88, 48.75]・二軸そろい 54.98%（C §5）。
- 窓の規約（`evidence/COMMON.md` 第2段）: 探索 2024-07-01〜2025-12-31、確認 2026-01-01〜2026-07-15、2026-07-16 以降は現状測定のみ。確認窓は検証 50 本以上で使用済み（AUDIT §0 でも同日付の決定）。
- 月次 vintage 方針（`vintage_model_policy.md`）: 2022-12-01〜2023-12-31 を全モデル共通のベース学習データに固定。2024-01 以降は月単位で、月 M のモデル = ベース + 2024-01〜M 前月末で学習し M 月のレースだけをスコアする。命名 `lgbm_wt_eval_mYYMM`/`lgbm_wt_win_mYYMM`（`data/models/` に m2401〜m2609 が実在）。期間定義の正本は `src/wt_vintage_config.py::monthly_windows()`。`save_model()` が凍結名への上書きを拒否（chmod 444 + `FileExistsError`）。過去日を本番モデルで採点しないための `assert_vintage_for_past` が `predict_p3_pw` の中にある（`build_type_lab_picks.py:353`）。
- 本番モデルの refit 状態: `lgbm_wt_eval` は `full_refit:false`（`test_from` が約 90 日前）、`lgbm_wt_win` は `full_refit:true`。後者を過去へ当てた数値は in-sample（LEAK §6）。

### 4.2 リーク／skew 関連の証拠の所在

| 論点 | 所在 |
|---|---|
| ローリング窓の point-in-time（`closed="left"`・同日 2 走 0 件） | LEAK §1 |
| `wt_entries` の節内不変（INSERT OR REPLACE の実害なし） | LEAK §2.1-2.2、C §4、AUDIT §2.3 |
| 並び・AI 印の朝欠測 | LEAK §2.3、AUDIT §8 #6 |
| race_point 破損 2026-06-12 | LEAK §2.4、AUDIT §8 #5、`evidence/P4_bugs/notes.md` |
| med_rp の母集団差と固定化 | LEAK §2.5、C §4、AUDIT §8 #7、`evidence/P4_bugs/fix_07_feature_wt_med_rp.patch` |
| オッズ由来特徴 0 列 | LEAK §3 |
| 学習=配信が同一関数 | LEAK §4 |
| `p3_calibration` の窓（2025 年・in-sample） | LEAK §5 |
| 朝の出力 vs 再計算（axis1 一致 94.57%・型境界またぎ 7.67%） | C §4 |
| 朝値の退避（2026-09-30 追加） | `scripts/snapshot_morning_entries_wt.py`（結果の記録は本リポジトリ内に無し） |

### 4.3 既存証拠が自ら「未計測」としている点（再掲）

LEAK §7: 朝のスナップショット不在のため朝値 vs 現在値の直接比較なし／生成側で退化入力のまま p3 が作られたレース総数／2026-06 の race_point 破損原因／予測オッズの学習入力(walk-forward p3)と配信入力(本番 p3)の分布差／in-sample 較正の偏り／`wt_odds` の上書き実態。
