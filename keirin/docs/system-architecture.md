> 🔴 **2026-09-20 監査**: 本書は 2026-08-03 から更新されておらず、型ラボの経路が丸ごと欠けている。現物の経路は `AUDIT_2026_09_20.md` §2。


# システムアーキテクチャ

> 最終更新: 2026-08-03（7B新設 / 7SS全廃 / ランク全面改名 `f31f84b`）

---

## 概要

競輪AI予想システム「穴車AI」。7車立て・9車立てレースを **7S / 7A / 7B / 9S / 9A**
（4内部rank・4表示ラベル。S1 は 2026-07-31 全廃・7SS は 2026-08-02 全廃）で
予想し、Discord 通知と netkeirin 自動入稿を行う CLI ベースのシステム。現行ランク体系の
詳細（ゲート条件・honest実績・沿革）は `../CLAUDE.md`「現行ランク体系」節、特徴量・モデルの
詳細は `prediction-factors.md` を参照（本ファイルは更新頻度が低いため、両ファイルと矛盾する
場合はそちらを正とする）。

**2ルート構成（2026-06-08 winticketへ完全移行）:**
- **winticket ルート（★本番稼働中）** — winticket.jp 経由。ライン情報・全組合せ事前オッズを取得。lgbm_wt（48特徴）。
- **keirin-station ルート（収集停止・ロールバック保持）** — keirin-station.com 経由。lgbm_v6（24特徴）。2026-06-08で収集凍結。

---

## ディレクトリ構成（実際）

```
keirin/
├── src/
│   ├── database.py                    # DBスキーマ・venue_infoマスタ・init_db/migrate_db
│   ├── scraper/
│   │   ├── keirin_station.py          # 競輪ステーション スクレイパー（requests+BS4）
│   │   ├── pipeline.py                # ks収集パイプライン（並列4会場・出走表+結果並列取得）
│   │   ├── winticket.py               # winticket スクレイパー（PRELOADED_STATE JSON解析）
│   │   └── pipeline_wt.py             # wt収集パイプライン（並列2会場・オッズ同時取得）
│   ├── preprocessing/
│   │   ├── feature_engineer.py        # FEATURE_COLS（24特徴量・ks/ロールバック）・build_features()
│   │   ├── feature_wt.py              # FEATURE_COLS_WT（48特徴量・rolling込/DNS処理済）・build_features_wt()
│   │   └── rolling_stats.py           # compute-stats（6ヶ月勝率・場別勝率・前走日数）
│   ├── strategy_wt.py                  # 波乱/非本命ゲート（top3_sum・upset_tier・passes_upset_gate）
│   ├── models/
│   │   └── trainer.py                 # train_lgbm/train_baseline/save_model/load_model
│   ├── prediction/
│   │   └── predictor.py               # predict_race・format_prediction
│   ├── evaluation/
│   │   ├── backtest.py                # run_backtest/run_threshold_analysis/run_day_simulation
│   │   └── upset_model.py             # 波乱レース予測モデル
│   ├── notify/
│   │   ├── discord.py                 # Discord Webhook 通知（send/send_file・6ch）
│   │   └── issue_list.py             # 課題リストの整形（上限・畳み込み・差分抑止）
│   └── cli/
│       └── main.py                    # CLIエントリーポイント（全コマンド定義）
├── scripts/
│   ├── daily_picks_wt.sh              # ★本番日次（cron 8:00・単一バッチ・当日全レース対象）
│   ├── evening_picks_wt.sh            # （2026-08-01 8:00一本化によりcron撤去・手動/アドホック実行専用）
│   ├── check_line_readiness.py        # ライン情報(winticket linePrediction)充足度判定（2026-08-01新設）
│   ├── intraday_results_wt.sh         # ★本番日中（cron 0,10-23時）当日結果逐次収集・通知なし
│   ├── weekly_retrain_wt.sh           # ★本番週次（cron 日23:30）
│   ├── nightly_review.sh              # ★型ラボ夜間レビュー（VPS cron 00:10・前日分）
│   ├── nightly_review_type_lab.py     # 夜間レビュー本体（§1〜§6・台帳は起点 REVIEW_EPOCH ごとに別ファイル）
│   │                                   # 起点 2026-08-29・2026-09-15（段の商品を売った日）だけ累積から除外
│   ├── nightly_report_html.py         # 夜間レビューの HTML（数字は本体の関数を呼ぶ）
│   ├── nightly_triage.sh              # Mac 側。Claude が §1〜§6 から課題を仕分ける
│   ├── notify_issues.py               # 夜間レビュー §1 の異常 → Discord(review)
│   ├── notify_picks.py                # wave-picks 通知 + PDF生成 → Discord
│   │                                   # （「朝夕の推奨」は2026-07-31廃止。日次/夕方cronからの呼び出しなし）
│   ├── notify_results_wt.py           # wt前日結果採点 + picks_history(route='wt') → Discord
│   ├── snapshot_morning_odds_wt.py    # 朝オッズ退避(wt_odds_snapshot) / --report ドリフト計測
│   ├── snapshot_intraday_odds_wt.py   # 日中オッズスナップショット（money-flow素材・G03）
│   ├── live_report_wt.py              # live実測レポート（ランク別/タグ別成績・ドリフト分布・標本数・G02）
│   ├── collect_weather.py             # 気象データバックフィル（Open-Meteo API・G05）
│   ├── exp_moneyflow_wt.py            # money-flow 検証ハーネス（G04）
│   ├── exp_wind_wt.py                 # 風×バンク特徴リーク無し検証（G06）
│   ├── exp_highpay_fusion_wt.py       # 高配当検知×新シグナル合成（ゲート判定・G07）
│   └── analyze_*/backtest_*_wt.py     # 各種検証スクリプト
├── data/
│   ├── keirin.db                      # SQLite DB（wt 96,455R + ks凍結 / WALモード）
│   ├── models/                        # lgbm_wt.pkl（=v1・本番）/ lgbm.pkl（=v6・ロールバック）等
│   └── picks/                         # wave_picks_wt_YYYY-MM-DD.txt / _detail.json / _detail.pdf
├── config/                            # 設定ファイル（.env: DISCORD_WEBHOOK_URL）
├── docs/                              # ドキュメント
├── notebooks/                         # Jupyter（探索・分析用）
├── tests/
├── requirements.txt
└── CLAUDE.md                          # 開発ガイド（このリポジトリ固有ルール）
```

---

## CLI コマンド一覧

### winticket ルート（★本番）

| コマンド | 説明 |
|---------|------|
| `status-wt` | 収集状況確認 |
| `collect-wt [--date]` | 1日分収集（レース+オッズ同時） |
| `collect-wt-range --from [--to]` | 年月範囲を逆順収集 |
| `train-wt [--from] [--test-from] [--save-as]` | winticket 用LightGBM学習（48特徴） |
| `backtest-wt [--from] [--to] [--model] [--max-riders] [--min-gap12] [--tiered] [--value]` | 買い目バックテスト（wt_odds 実オッズ使用） |
| `wave-picks-wt [--date] [--min-trio-odds] [--gami-skip-odds] [--b-rank-odds] [--upset-gate]` | 7S/7A/7B/9S/9A 候補生成＋ガミ3段階／波乱ゲート（詳細は`../CLAUDE.md`「現行ランク体系」節） |

**wave-picks-wt の主要フラグ（2026-06-08 追加）:**
- `--gami-skip-odds 3.0`：3点中1点でも朝オッズ<3倍ならレース見送り
- `--b-rank-odds 5.0`：最安目が3〜5倍未満ならBランク（購入は各自判断・別枠）
- `--upset-gate Q1_loose|Q2|Q3`：top3_sum波乱ゲート（opt-in。省略時は全pickに upset_tier タグ付けのみ）
- `--stake-tilt`：波乱スコア(top3_sum)で賭け金傾斜（opt-in・既定off）
- `--ss-trifecta-box`：SS層の3連単を pred1,pred2 1-2着BOX(6点)に拡張（opt-in・既定off=3点で本番不変。検証=`docs/analysis/10-le6-fav-position.md`）

補助スクリプト:
- `scripts/snapshot_morning_odds_wt.py [date]`（朝オッズ退避）/ `--report`（朝→最終ドリフト計測）
- `scripts/snapshot_intraday_odds_wt.py [--date]`（日中オッズスナップショット・money-flow素材）
- `scripts/live_report_wt.py [--from] [--to] [--format md]`（live実測レポート・ランク別/タグ別成績・ドリフト分布・必要標本数推定）
- `scripts/collect_weather.py [--from] [--to]`（気象データバックフィル・全43会場・Open-Meteo Historical API）
- `scripts/exp_moneyflow_wt.py [--from] [--to] [--report]`（money-flow検証ハーネス・ドリフト記述統計・スマートマネー仮説）
- `scripts/exp_wind_wt.py [--from] [--to]`（風×バンク特徴のリーク無し LGBM 検証）
- `scripts/exp_highpay_fusion_wt.py [--report]`（高配当×新シグナル合成・G06/G04ゲート判定）

### keirin-station ルート（収集停止・ロールバック保持）

`init` / `status` / `collect[-month/-range/-reverse]` / `compute-stats` / `train` / `backtest` / `analyze` / `weekly` / `day-sim` / `venue` / `predict` / `wave-picks` / `upset-train` / `upset-backtest`（2026-06-08 以降 日常運用では未使用）。

---

## データフロー

### keirin-station ルート

```
keirin-station.com
  └── scraper/keirin_station.py  (requests + BS4)
        └── scraper/pipeline.py  (4会場並列 / 出走表+結果並列)
              └── database.py    (races / race_entries / race_results / odds)
                    └── preprocessing/feature_engineer.py  (FEATURE_COLS 24特徴量)
                          └── models/trainer.py  (LightGBM 時系列CV)
                                └── data/models/lgbm.pkl
                                      └── cli wave-picks
                                            └── scripts/notify_picks.py → Discord
```

### winticket ルート

```
winticket.jp (PRELOADED_STATE JSON / SSR)
  └── scraper/winticket.py  (requests / tanStackQuery解析)
        └── scraper/pipeline_wt.py  (2会場並列 / レース+オッズ同時取得)
              └── database.py    (wt_races / wt_entries / wt_odds)
                    └── preprocessing/feature_wt.py  (FEATURE_COLS_WT 48特徴量・rolling込/DNS処理済)
                          └── models/trainer.py  (同一trainer / feature_cols引数)
                                └── data/models/lgbm_wt.pkl
                                      └── cli wave-picks-wt (オッズフィルター付き)
```

---

## DBスキーマ（概要）

### keirin-station テーブル

| テーブル | 内容 |
|--------|------|
| `races` | レース情報（race_key, venue_code, race_date, grade, distance, start_time） |
| `race_entries` | 出走情報（24カラム: racing_score, gear_ratio, win_rate, 脚質 等） |
| `race_results` | 着順結果（frame_no, finish_position） |
| `odds` | 払戻金（bet_type: trifecta/trio/quinella 等, payout） |
| `venues` | 会場マスタ |
| `players` | 選手マスタ |
| `venue_info` | 場マスタ（bank_length, is_indoor, prefecture） |
| `picks_history` | 予想履歴（hit/payout 集計用） |

### winticket テーブル

| テーブル | 内容 |
|--------|------|
| `wt_races` | レース情報（cup_id, day_index, grade, start_at 等） |
| `wt_entries` | 出走情報（34カラム: race_point, 脚質, lineup情報, 戦術率, finish_order 等） |
| `wt_odds` | 事前オッズ（bet_type: trifecta/trio/exacta/quinella 等, odds_value・最終値で上書き） |
| `wt_odds_snapshot` | オッズスナップショット（snapshot_type='morning'/'h06'/'h10'等・初回値保持。朝→最終ドリフト計測・money-flow用） |
| `wt_weather` | 気象データ（venue_id×dt_hour PK・wind_speed/wind_gust/temperature 等。Open-Meteo API 経由バックフィル済） |

---

## 技術スタック

| レイヤー | 技術 |
|---------|------|
| スクレイピング | Python 3.10, requests, BeautifulSoup4 |
| データ管理 | SQLite 3（WALモード）, pandas |
| ML | LightGBM, scikit-learn（baseline用） |
| CLI | Click |
| 通知 | Discord Webhook（urllib.request で実装 / requests不使用） |
| PDF生成 | matplotlib（PNG変換）+ Pillow（PDF結合） |
| 環境管理 | venv（`.venv/`）|

---

## 毎日の自動実行フロー（2026-09-22 に旧ランクを破棄して型ラボ一本にした）

**VPS（`/home/ysuzuki/GitHub/kiseki/keirin`）が自前で cron を実行**
（Mac 側は `weekly_retrain_wt.sh` のみ）。

🔴 **2026-09-22: 旧ランク16種の生成・採点・再構築を本番から外した**（ユーザー決定
「旧ランクは破棄し、型ラボのみに整理、最適化」）。旧ランクは 2026-08-28 を最後に
1件も入稿しておらず（`netkeirin_settings.enabled` が全て false）、それでも
**1日あたり約2時間**を使っていた。外したもの:

| 何を | いつ | 実測の所要 |
|---|---|---|
| `wave-picks-wt` / `reselect_7s_evening.py` / `write_candidates_wt.py` | 07:00 の中 | 7分18秒 |
| `reconcile_walkforward_tail.sh`（旧ランクの当月再構築） | 08:40 | **1時間37分** |
| `evening_picks_wt.sh`（夜レースの再生成） | 16:00 | 十数分 |
| `notify_prerace_wt.py`（発走15分前のライブ判定） | 毎分 8-23時 | 2026-08-28 以降は無出力 |
| `backfill_missing_prerace_wt.py`（RANK_7S の欠損補完） | 00:40 | — |
| `notify_results_wt.py`（`picks_history` の採点＋Discord ダイジェスト） | 06:30 / 07:00 / 15分ごと | 前日ぶんで約9分 |
| `monitor_wide_wt.py`（廃止したワイドのドリフト監視） | 06:30 / 07:00 | — |

ファイルは**消していない**（過去分析の台として有効）。退役したバッチは
`KEIRIN_ALLOW_OLD_RANKS=1` を付けたときだけ動く。復活防止は
`tests/test_old_ranks_retired.py` が固定している。

```
06:30 previous_day_wt.sh
  ① collect-wt --date $(yesterday) --full-scan   # 前日の着順・確定オッズ（型ラボの採点の入力）
  ② 結果バックフィル（T-2〜T-4 の取りこぼし回収）

07:00 daily_picks_wt.sh
  ① collect-wt --date $(today) --full-scan       # 当日の出走表・オッズ・race_point（全会場走査）
  ② check_race_point_sanity.py                   # 異常値なら5分待って最大3回再収集 → 駄目なら中断
  ③ check_line_readiness.py                      # ライン未公開が多ければ同様に再収集（続行はする）
  ④ snapshot_morning_odds_wt.py                  # 朝オッズを wt_odds_snapshot へ退避
  ⑤ type_lab_daily.sh
       ⑤-1 type_lab_morning.py                   # 🔴 1プロセスで4つ（特徴量は1回だけ作る）
              ├ 7車の買い目（mode='live'）
              ├ 9車の買い目（mode='live9'）
              ├ race_shapes（表示用の型・全車数）
              └ wt_entries.pred_{win,top2,top3}_pct（出走表の指数）
       ⑤-2 netkeirin_submit_type_lab.py morning  # 入稿 → 自動公開 → 自信あり選定 → 通知
       ⑤-3 settle_type_lab_picks.py              # 前日・当日・保留ぶんの採点
  ⑥ 前日処理の保険（06:30 が落ちた日だけ ①② 相当を実行）
  ⑦ migrate_sqlite_to_pg.py                      # VPS PostgreSQL 同期

13:05 / 18:05 type_lab_wave.sh {noon|evening}
  build_race_shapes.py → netkeirin_submit_type_lab.py
  （朝に並び予想・AI印が未公開だったレースを組み直して入稿する）

15分ごと（8-23時,0時）
  intraday_results_wt.sh   当日結果の逐次収集（collect-wt）＋ PG 同期
  type_lab_settle.sh       型ラボの採点（前日＋当日）
毎分（8-23時）  notify_race_result_wt.py   売ったレースの結果を確定直後に Discord へ
00:10  nightly_review.sh    型ラボの夜間レビュー（HTML＋Discord）
週次（日 23:30・Mac）  weekly_retrain_wt.sh
```

**朝バッチの所要（実測・2026-09-22 の朝）**: 旧ランク破棄と特徴量の1回化の前は
**24分52秒**で、うち `build_features_wt`（1回 約6分50秒）が**3回**走っていた。
`type_lab_morning.py` へまとめた後は1回で済む。

現行ランクは以下の**5内部rank / 5表示ラベル**（2026-08-03時点）:

| 内部rank | suffix | 表示 | 内容 |
|---|---|---|---|
| `RANK_7S` | `#7S` | 7S | 7車・三連複2軸総流し・本流（全ゲート合格） |
| `RANK_7A` | `#7A` | 7A | 7車・境界（ゲート1つのみ不合格） |
| `RANK_7B` | `#7B` | 7B | 7車・◎◯一致だが順序/相手で不一致・**三連複3点（相手絞り）** |
| `RANK_9S` | `#9S` | 9S | 9車・本流（RANK_7S の9車版・独立ランク） |
| `RANK_9A` | `#9A` | 9A | 9車・境界 |
| `RANK_7C` | `#7C` | 7C | 7車・**ベースモデル（終日の二軸）**・三連複 軸2車＋相手可変4〜5点・予算枠10,000円/レース |
| `RANK_7H1` | `#7H1` | 7H1 | 7車・穴推奨（本命バスト型）・三連単F8点（2026-08-15 に三連単一本化） |

**賭け金は全ランク「1レース10,000円を点数で均等割り」**（2026-08-07 統一・
`src/strategy_wt.py` の `RACE_BUDGET` / `unit_stake()` が単一正本）。
5点→2,000円 / 4点→2,500円 / 3点→3,300円 / 7点→1,400円。
**欠車で点数が減ったレースも予算枠に揃う**（従来の固定単価では投資が目減りしていた）。

⚠️ **`RANK_7C` だけは他ランクと論理的に排他ではない**（`wt_overlap_n` を見ないため
同一レースに併存しうる）。`picks_history.race_key` は `{レースキー}#{suffix}` なので
行は共存でき、**候補生成・記録の段階では重複を排除しない**。1レース1商品という
netkeirin の制約は入稿側だけで解決する（優先順位 **7H2 > 7T1 > 7T3 > 7S > 7B > 7C > 7H1 > 7M1**、
`netkeirin_submit_wt.RANK_CONFIGS` の定義順が正本）。

全廃済み: 7SS(`RANK_7SS`・穴レース検知・2026-08-02。live実績 n=16,298・ROI73.5%で
控除率75%割れが続いたため。判定ロジックは再設定に備え残置)・
S1(`SEVEN_S1`・2026-07-31)・S2/S3(`7PLUS_U`/`7PLUS_M`・2026-07-21)・
6車三連単S1(`SIX_S1`)・A・旧SS(`7PLUS_R`)・S/S+(`7PLUS_ST`/`STP`)・
`gate_label`による SS/S 分割表示（2026-07-31にSへ統合）。
詳細は`prediction-factors.md` / `../CLAUDE.md`「現行ランク体系」節。

内部rank名・suffixは 2026-07-31 の commit `f31f84b` で `SEVEN_S7`→`RANK_7S`・
`NINE_S9`→`RANK_9S` 等へ全面改名済み（**表示ラベルは
Web と揃えるため変更なし**）。規則は 内部rank = `RANK_` + 表示ラベル、
suffix = `#` + 表示ラベル。旧名対応は `src/strategy_wt.py` の
`LEGACY_RANK_NAME_MAP` / `LEGACY_SUFFIX_MAP` を参照。

### モデル配布・鮮度監視（D-5/D-6, 2026-07-31追加）

**D-5: 月次vintageモデルのVPS配布漏れ対策**

従来、Mac crontabの`weekly_retrain_wt.sh`実行行の末尾に直接`rsync`コマンドが
書かれており、転送対象ファイルを長いコマンドラインで明示列挙していた
（`lgbm_wt.pkl`/`lgbm_wt_train_only.*`/`lgbm_wt_win.*`/`lgbm_wt_eval.*`/
`lgbm_wt_win_eval.*`/`upset_cuts_wt.json`のみ）。`docs/vintage_model_policy.md`
記載の月次vintageモデル（`lgbm_wt_eval_m????.pkl`/`lgbm_wt_win_m????.pkl`とその
`.meta.json`、2026-07-30時点62本104MB）はこの列挙に含まれておらず、2026-07-30に
一度きり手動rsyncで配布されたのみだった。`train_monthly_vintage_models.py
--only-missing`で新しい月のモデルを追加作成しても、この構造のままではVPS側は
今後も受け取れない。

対策として `scripts/sync_models_to_vps.sh` を新設し、転送対象ファイルの列挙を
crontab（変更漏れの温床）からスクリプト側に一本化した。月次vintageモデルは
ファイル名パターン（`lgbm_wt_eval_m[0-9][0-9][0-9][0-9].pkl`等）で動的に検出する
ため、新しい月のモデルが増えてもスクリプトの変更は不要。転送後に
(1) `rsync --checksum --dry-run` によるチェックサム照合、
(2) VPS側ファイル数照合、の2段階で到達を検証し、失敗時は
`src/notify/discord.py::send(msg, channel="system")` で通知する。
`--dry-run` オプションで実転送なしに対象ファイル一覧を確認できる。

**Mac crontab への反映は 2026-08-01 に完了した**（ユーザー承認のうえ実施。
反映前の内容は Mac 上の `~/crontab_mac_backup_20260801.txt` に保全）。
適用した差分（⚠️ **パスは 2026-08-10 の kiseki 統合で `~/GitHub/keirin` から
`~/GitHub/kiseki/keirin` へ移設済み**。以下は現行パスに直して記載している。
当時の実際の crontab は旧パスだった。退避 `~/crontab_mac_before_keirin_merge_20260810.txt`）:

```
# 変更前（〜2026-08-01）:
30 23 * * 0 ~/GitHub/kiseki/keirin/scripts/weekly_retrain_wt.sh \
  >> ~/GitHub/kiseki/keirin/data/logs/cron.log 2>&1 && \
  rsync -av <個別ファイルを明示列挙...> sekito:~/GitHub/kiseki/keirin/data/models/ \
  >> ~/GitHub/kiseki/keirin/data/logs/cron.log 2>&1

# 変更後（現行・2026-08-01〜）:
30 23 * * 0 ~/GitHub/kiseki/keirin/scripts/weekly_retrain_wt.sh \
  >> ~/GitHub/kiseki/keirin/data/logs/cron.log 2>&1 && \
  ~/GitHub/kiseki/keirin/scripts/sync_models_to_vps.sh \
  >> ~/GitHub/kiseki/keirin/data/logs/cron.log 2>&1
```

あわせて `scripts/ensure_monthly_vintage.sh`（不足月の学習 + VPS配布）を月初に
実行する行も Mac crontab へ追加した（同スクリプトのヘッダが推奨していたエントリ。
`5 0 1 * *` = 毎月1日 00:05。週次retrain の日曜23:30 と重ならず、
`reconcile_walkforward_tail.sh`(00:50・現在PAUSED) より前）:

```
5 0 1 * * ~/GitHub/kiseki/keirin/scripts/ensure_monthly_vintage.sh \
  >> ~/GitHub/kiseki/keirin/data/logs/cron.log 2>&1
```

これにより、月次vintageモデルが「学習はされるが配布されない」「月が替わった
瞬間に当月モデルが無くて rebuild が落ちる」という 2026-08-01 に実害化した
2つの穴が恒久的に塞がる。

**D-6: 週次再学習がスリープでスキップされても検知できない問題への対策**

2026-07-31実機確認で `pmset -g sched` はスケジュール済みwake/poweron 0件だった。
日曜23:30にMacがスリープ/シャットダウンしていると週次再学習・モデル配布が
丸ごとスキップされるが、`weekly_retrain_wt.sh`のAUCゲートは「今週実行されたか
どうか」自体は検知できず、通知も発生しないため気付かれにくい。

方向A（VPS側からの実行検知）として `scripts/check_model_freshness.py` を新設した。
本番モデル4種（`lgbm_wt.pkl`/`lgbm_wt_eval.pkl`/`lgbm_wt_win.pkl`/
`lgbm_wt_win_eval.pkl`。週次再学習で再生成されない`lgbm_wt_train_only.pkl`は
対象外）のmtimeを見て、既定8日（週次周期7日+1日の実行猶予）を超えて
更新が無ければDiscord（systemチャンネル）へ警告する。VPSはメモリ1.9GB・
空き実測101MB程度と逼迫しているため、`os.stat`によるmtime比較のみで
pandas/lightgbm等の重い依存はimportしない設計にしている。**VPS cronへの
登録は本タスクでは行っていない**（登録すればVPS crontabの変更が必要なため、
以下のコマンドを提示するに留める。実際の登録はユーザー判断で実施すること）:

```
0 9 * * * cd $KEIRIN_HOME && PYTHONPATH=. .venv/bin/python3 \
  scripts/check_model_freshness.py >> $KEIRIN_HOME/data/logs/cron.log 2>&1
```

方向B（`pmset repeat wakeorpoweron`によるスリープ対策）は、2026-07-31時点で
`pmset -g sched` が空である（＝現状このMacにはDBバックアップ用も含めて
wake/poweronスケジュールが一切設定されていない）ことを確認した。`man pmset`
によれば `pmset repeat` は**システム全体でwake系・sleep/shutdown系それぞれ
1本ずつしかスケジュールを保持できない**仕様のため、「日曜23:25起床」と
「毎日03:25起床（kiseki側DBバックアップ想定）」を別々の時刻・別々の曜日指定で
共存させることはできない。両立させたい場合は**毎日同一時刻に統一したwake**
（例: 毎日23:25に起床。当該日23:30の週次再学習と、翌日03:30のDBバックアップの
両方を1回の起床でカバーできる）にする必要がある。加えて、本機は現在
`pmset -g` で `SleepDisabled 1`（アイドルによる自動スリープが無効化された状態）
であることを確認済みで、これが維持されている限りアイドルスリープ由来の
スキップリスクは実質的に低い。ただし手動スリープ・蓋閉じ・再起動・停電等の
リスクは残るため、方向Aの検知は方向Bの有無に関わらず有効である。
`sudo`が必要なため、以下は実行コマンドの提示のみで実際の設定はユーザーが行うこと:

```
sudo pmset repeat wakeorpoweron MTWRFSU 23:25:00
```

---

## 開発経緯（簡略）

| 時期 | 内容 |
|------|------|
| 2026-02 | v1.0 本番稼働（LightGBM 13特徴量 / AUC 0.7444） |
| 2026-05 | v2〜v4: 特徴量24個・時系列CVへ修正・データ拡張 |
| 2026-06-02 | wave-picks SS/S/A 3段階ランク戦略策定 |
| 2026-06-04 | ks v6: 2023年〜追加収集 / AUC 0.7575 / ホールドアウト9ヶ月検証 |
| 2026-06-05 | S ランクに ratio<1.6 上限追加（低配当レース除外） |
| 2026-06-07 | winticket 全期間収集（96k）。ローリング特徴移植・ks比較検証 |
| **2026-06-08** | **DNS(欠車)バグ修正 → winticket本番移行**（lgbm_wt_v1・39特徴・CV AUC 0.7720）。3タスク分析（`docs/analysis/`）→ 波乱ゲート(`strategy_wt.py`)・ガミ回避3段階・朝オッズ前向き計測を実装 |
| **2026-06-09** | n_senko 特徴追加（FEATURE_COLS_WT 39→40）。SS三連単BOX(6点)・ワイド1点推奨(opt-in)実装。7+クローズ（公開オッズ内を3経路で確定）。fav_mismatch タグ記録開始。夕方2段階生成（`evening_picks_wt.sh`）実装。 |
| **2026-06-10** | 欠車無効化（`notify_results_wt._void_by_dns`）・結果バックフィル実装。会場取得漏れバグ修正（ks references → wt_races）。`linePrediction=null` クラッシュ修正。 |
| **2026-06-12** | バックテスト3バイアス発見（`docs/analysis/18`）。夕方cron 16:00登録完了。各種検証スクリプト追加（gap13打ち切り・B閾値緩和全滅・条件先行新方式なし・高配当10点リーク無し不通過・コメント特徴無情報・Web予想監査不通過）。 |
| **2026-06-13** | G01〜G07完了（`backtest_wt.py`リーク無し化・live実測レポート・日中オッズスナップショット・money-flow検証ハーネス・気象データ収集・風特徴検証・高配当融合ゲート）。 |
| **2026-08-01** | ライン情報(winticket linePrediction)充足度チェック `scripts/check_line_readiness.py` 新設。**当初は「7:00(日中)+8:00(夜)」の2バッチ体制で検討したが、PM実測（直近92日で1日の最初の発走が全日08:30・8:00より前に発走する日は0日）を踏まえたユーザー判断により、8:00の単一バッチへ一本化**（`daily_picks_wt.sh`の`wave-picks-wt`から`--start-to-hour`指定を撤去し当日全レースを対象化。`evening_picks_wt.sh`はcronから撤去し手動/アドホック実行専用に）。ライン欠損リトライ（対象レースの30%超でライン未公開なら5分待機して再収集、最大3回・解消せずともexitせず続行）は単一バッチにも維持し、時刻指定なし（当日全レース）で判定する。S7の日次件数上限(`RANK_7S_DAILY_CAP=12`、`rank_7s_daily_select()`自体では未適用の安全網)を適用する`reselect_7s_evening.py`の呼び出しを`evening_picks_wt.sh`から`daily_picks_wt.sh`へ移設（night_raw相当が空でも日次トリムとして機能）。netkeirin入稿は`session="morning"`の1回に統合（`_load_candidates()`がsession="morning"時に読む非"_night_"ファイルに当日全レース分が書かれるため重複・欠落なし）。VPS crontab の `0 16 * * * evening_picks_wt.sh` 行の撤去も**同日ユーザー承認のうえ反映済み**（撤去前の内容は VPS 上の `~/crontab_backup_20260801.txt`）。 |
