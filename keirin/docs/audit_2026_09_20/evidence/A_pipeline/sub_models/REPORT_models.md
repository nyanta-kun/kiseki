# 本番モデルの実体 — 監査レポート（依頼A 項目2） 2026-09-20

証拠は本番コード（呼び出し元まで）・実ファイル・自分で走らせた計算のみ。

## 0. 朝の経路
VPS crontab 実測: `0 7 * * * daily_picks_wt.sh` / `0 16 * * * evening_picks_wt.sh` / `5 13,18 type_lab_wave.sh`。
- daily_picks_wt.sh:225 → `src.cli.main wave-picks-wt`
- daily_picks_wt.sh:395 → `scripts/type_lab_daily.sh` → build_type_lab_picks.py --mode live (7車/9車) → netkeirin_submit_type_lab.py
- 旧ランクの netkeirin 入稿呼び出しはシェルに1本も無い（daily_picks_wt.sh:378-390 で 2026-08-30 に除去）。
- 旧ランク候補生成 7H1/7H2/9H1/7T1/7T3 は daily_picks_wt.sh:278-319 でコメントアウト。

## 1. 朝に実際に読まれるモデル
### 1-A 選手単位ヘッド (pickle, load_model = src/models/trainer.py:217, MODEL_DIR=trainer.py:20)
| 役割 | 名前 | 読む場所 | サイズ | Mac mtime | VPS mtime |
|---|---|---|---|---|---|
| p3 型ラボ(売る商品) | lgbm_wt_eval | build_type_lab_picks.py:224,245 / :254 | 1,781,366 | 09-13 23:58 | 09-13T23:58 |
| pw 型ラボ | lgbm_wt_win | 同 :225,246 | 1,772,130 | 09-14 01:46 | 09-14T01:46 |
| p3 Web指数/旧ランク | lgbm_wt | src/cli/main.py:1287(既定),1455 | 1,781,916 | 09-14 00:39 | 09-14T00:39 |
| 2着内(表示専用) | lgbm_wt_top2 | src/cli/main.py:1486 | 1,778,828 | 09-14 02:58 | 09-14T02:58 |
| 大敗 bad6 | lgbm_wt_bad | src/cli/main.py:1499 | 1,779,695 | 09-10 16:06 | 09-10T16:06 |

### 1-B 予測オッズ (LightGBM Booster txt)
| 券種 | ファイル | 読む場所 | サイズ | mtime | trees | 特徴 | objective | train_end |
|---|---|---|---|---|---|---|---|---|
| 三連単7車 | odds_tf_n7.txt | odds_prediction_tf.py:258-263 | 4,348,772 | 08-12 22:27 | 700 | 63 | regression/l1 | 2025-12-31 |
| 三連単9車 | odds_tf_n9.txt | 同 | 3,732,817 | 08-28 06:10 | 600 | 63 | 同 | 2025-12-31 |
| 三連複7車 | odds_trio_n7.txt | odds_prediction.py:330-337 | 8,699,854 | 08-20 10:17 | 700 | 52 | regression | 2026-08-04 |
| 三連複9車 | odds_trio_n9.txt | 同 | 4,266,180 | 08-20 10:29 | 700 | 52 | 同 | 2026-08-04 |

型ラボは odds_tf のみ。三連複オッズは build_type_lab_picks.py:82 `_fold_to_trio` が三連単板から導く。
odds_trio の生きた消費者は src/confident_pick.py:103-107,442-443（自信あり）だけ。

### 1-C 較正 src/p3_calibration.py（実体 backend/src/services/keirin_p3_calibration.py を動的import）
🔴 型ラボは較正を通さない（type_lab.py:57-58・AXIS_SUM_FIRM=1.44 は生p3）。
較正の消費者は race_gate_7c.py:55 と cli/main.py:1345,2097,2427＝いずれも現在入稿されない経路。

### 1-D 死んだ資産（配布はされるが朝に読まれない）
lgbm_wt_favbust.pkl(build_7h1_candidates.py:97・呼び出しコメントアウト) /
lgbm_upset_screen.pkl(build_9h1_candidates.py:99・同) /
lgbm_wt_train_only / *_eval(top2,win)＝監視用 / lgbm_v*,lgbm_pair,lgbm_upset*,baseline /
月次 vintage 324ファイル＝backfill・検証専用（run_live に vintage を渡す経路なし）。

## 2. 特徴量
FEATURE_COLS_WT = **70列**（実測 len=70・全 meta の feature_count=70）。「60特徴」は誤り。
区分: コア得点/エンコード6・枠/レース内相対7・場グレード3・選手属性2・ライン構造13・
S/H/B回数3・AI印1(prediction_mark)・直近rolling7・得点トレンド6・S/B上がりrolling4・
隊列2・レース種別7・ライン実力5・節内成績4。
🟢 オッズ由来列ゼロ。市場情報は AI印 prediction_mark のみ（feature_wt.py:1332「市場人気の代理変数」）。
odds_tf: FEATURE_NAMES 63（odds_prediction_tf.py:77-104）。load_meta():239-253 が名前と順序の完全一致を照合。
odds_trio: 52列。

## 3. 学習窓
weekly_retrain_wt.sh（Mac cron 日曜23:30・crontab 実測）:
① `train-wt --from 2022-12-01 --test-from $TEST_FROM --save-as lgbm_wt_eval --no-promote`（TEST_FROM=実行日-90d）
①' AUCゲート（下限0.75・前回比-0.02）
② `--full-refit --save-as lgbm_wt`
②' win: _eval → ゲート → full-refit lgbm_wt_win
②'' top2: _eval → full-refit lgbm_wt_top2
③ recompute_upset_cuts_wt.py ④ archive 退避
🔴 `--target bad` の段が無い＝lgbm_wt_bad は週次対象外（実際 09-10 で止まっている）。

meta 実測: lgbm_wt_eval は full_refit=false / test_from=2026-06-15 / fit_rows 686,152、
lgbm_wt は full_refit=true / fit_rows 735,256（差 49,104 行 ≒ 直近90日）。

ハイパラ src/models/trainer.py:61-77: binary/auc, n_estimators=500, lr=0.05, num_leaves=31,
min_child_samples=20, colsample_bytree=0.8, seed=42。subsample は記述なし（:66-72 で削除済）。
日付ベース時系列CV 5分割＋early_stopping(50) は AUC 表示のみ。
**最終モデルは trainer.py:120-123 で全データ・early stopping なし・500木固定で再学習**。

keirin_protocol.py: TRAIN_FROM=2024-04-01(:69) / TEST_START=当四半期初日=2026-07-01(:78-82)。
🔴 weekly_retrain_wt.sh は --from 2022-12-01 のベタ書きで protocol と接続されていない。
🔴 lgbm_wt/win/top2/bad(full_refit) は TEST 期間を学習に含む＝これらが書いた過去予測は honest でない。
🟢 lgbm_wt_eval は test_from=2026-06-15 で TEST_START より前から切れており、型ラボの p3 は今四半期 OOS。
🟢 odds_tf は train_end=2025-12-31 で 2026年は全部 OOS。

odds_tf 学習（scripts/train_odds_prediction_tf.py）:
- y = log10(確定三連単オッズ)（:251）。教師は keirin.wt_odds bet_type='trifecta' の
  DISTINCT ON ... ORDER BY collected_at DESC（:105-114）＝最終スナップショット。
- 学習時の p3/pw は walk-forward（data/exp_cache/axis_detail_7car.pkl・wf_preds9_*.pkl :131-168）。
- 分割 :252-253（tr=date<=train_end）。early stopping なし・rounds 700 固定。
- predict_board :335-348 が Σ(1/o) を target_sum（7車1.33645/9車1.33316）へ再スケール。
- 🔴 自動再学習の経路は存在しない（weekly_retrain_wt.sh / ensure_monthly_vintage.sh に呼び出し grep 0件）。

## 4. 配布
sync_models_to_vps.sh:113-147 PROD_FILES＋:172-198 vintage glob → sekito:~/GitHub/kiseki/keirin/data/models/。
転送後 checksum dry-run 照合＋リモート ls 名前突き合わせ。Releases 配布は既定 OFF。
VPS 実測: 主要モデル全て Mac と mtime・サイズ完全一致。vintage も 324件 対 324件で一致。
🟢 週次再学習→配布は届いている（最新 2026-09-13(日)23:30 起動の回）。
vintage は src/wt_vintage_config.py:109-165 経由で rebuild_*/backfill_*/run_paper_vintage のみ。本番配信には未使用。

## 5. 予測オッズの train/serve
| | 学習 | 配信(型ラボ) |
|---|---|---|
| y | log10(確定三連単オッズ) | — |
| p3/pw | walk-forward キャッシュ | **lgbm_wt_eval / lgbm_wt_win をその場で推論**（build_type_lab_picks.py:245-246→:296） |
| 出走表/ライン/印 | wt_entries | wt_entries |
🔴 predicted_trifecta_board（odds_prediction_tf.py:354-370）は wt_entries.pred_* = lgbm_wt 由来を使う。
   呼び出し元は netkeirin_submit_wt.py:1203 のみで現在 cron に無い。
🔴 三連複 odds_prediction.py:398-439 は常に wt_entries.pred_*（=lgbm_wt）。
   ＝「自信あり」の合成オッズ判定は lgbm_wt の p3、買い目本体は lgbm_wt_eval の p3。

## 6. 🔴 本番に2つの p3 が併存
| 消費者 | p3 の出どころ | 学習終端 |
|---|---|---|
| 型ラボの型判定・買い目・配分（売る商品） | lgbm_wt_eval | 実行日-90日（現行 2026-06-15） |
| wt_entries.pred_top3_pct（Web 指数表示） | lgbm_wt | 実行日まで |
| 三連複予測オッズ→自信あり選定 | wt_entries = lgbm_wt | 実行日まで |
| 旧ランクの p3_sum_top2_cal ゲート | lgbm_wt ＋較正 | 実行日まで |

実測（sub_models/cmp_models.py・7車のみ）:
```
2026-09-19  55R: corr 0.9978 MAE 0.0120  firm(>=1.44) eval 31 / wt 29  判定不一致 4/55  上位2車集合不一致 4/55
2026-09-15  79R: corr 0.9977 MAE 0.0117  firm(>=1.44) eval 32 / wt 33  判定不一致 1/79  上位2車集合不一致 3/79
合計 134R:  型（堅い/混戦）判定不一致 5/134 = 3.7%  上位2車集合不一致 7/134 = 5.2%
```

## 7. 確認できなかった点・限界
1. 上の比較は 2日・134レースのみ。3.7% の二項95%CI ≒ [1.2%, 8.5%] で広い。
2. 型ラボの p3 に lgbm_wt_eval を選んだのが意図的かはコードから判定できない。
3. odds_tf の train_end 据え置きが honest 維持の意図か再学習経路欠如かは不明（経路が無いことのみ確認）。
4. odds_tf_meta.json の最上位 train_end/n_train_races/metrics は最後に学習した9車の値で上書き
   （最上位 n_train_races=3536 は9車の値・7車は per_n_car.7=7145）。model_train_end() は
   per_n_car 優先なので実害なし。
5. strace 等でファイルオープンを直接確認したわけではない（コードパス＋mtime/サイズ一致からの推定）。
