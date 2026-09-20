# 依頼A — 本番競輪予想パイプラインの「現物仕様書」（コード監査）

監査日: 2026-09-20 / 監査対象コミット: `f5c435f6`（VPS `ssh sekito` で `git log -1` を実測。
ローカル `main` と同一 SHA。VPS 側の未追跡ファイルは keirin 配下に無し）
方針: ドキュメントは証拠として扱わない。証拠は ①本番コード（呼び出し元まで追跡）
②DB 実データ ③自分で走らせた計算 のみ。

---

## 0. 経路の確定方法（証拠）

VPS の crontab を読み取りで取得（`ssh sekito 'crontab -l'`）。
リポジトリ内の `keirin/data/cron_*.txt` は **2026-06 時点の写しで、パスが
`~/GitHub/keirin`（統合前）のまま＝現物ではない**。以下はすべて実 crontab から。

| JST | エントリ | 実体 |
|---|---|---|
| 06:30 | `$KEIRIN_HOME/scripts/previous_day_wt.sh` | 前日の結果再収集・採点通知 |
| **07:00** | `$KEIRIN_HOME/scripts/daily_picks_wt.sh` | **当日の本番バッチ（この中で型ラボを呼ぶ）** |
| 08:30/14:30/19:30 | `scripts/notify_pending_approvals_wt.py` | 未承認入稿の催促（承認制 ON のとき） |
| 08:40 | `scripts/reconcile_walkforward_tail.sh` | picks_history の当月 walk-forward 再構築 |
| 09:15 | `scripts/check_model_freshness.py` | モデル mtime 監視 |
| 10,12,14,18,20:00 | `scripts/snapshot_intraday_odds_wt.py` | オッズスナップショット |
| `*/15 8-23,0` | `scripts/intraday_results_wt.sh` | 当日・前日の結果収集 |
| `* 8-23` (毎分) | `scripts/notify_prerace_wt.py` / `notify_race_result_wt.py` | 直前・結果通知 |
| 13:05 / 18:05 | `scripts/type_lab_wave.sh noon\|evening` | 型ラボの昼・夕の波（朝に出せなかったレース） |
| 16:00 | `scripts/evening_picks_wt.sh` | 朝に情報不足だったレースの第2パス（旧ランク側） |
| `5,20,35,50 8-23,0` | `scripts/type_lab_settle.sh` | 型ラボの採点 |
| 00:10 | `scripts/nightly_review.sh` | 前日レビュー |
| 01:20 | `scripts/collect_race_conditions_daily.sh` | 走路条件 |
| 00:40 | `scripts/backfill_missing_prerace_wt.py` | picks_history 欠損補完 |

Mac 側 crontab（`crontab -l` 実測）:
- 日曜 23:30 `keirin/scripts/weekly_retrain_wt.sh` → 成功時のみ `scripts/sync_models_to_vps.sh`
- 毎月1日 00:05 `keirin/scripts/ensure_monthly_vintage.sh`

---

## 1. 今日の朝に実際に動く経路（07:00）

`scripts/daily_picks_wt.sh` の実行順（file:line は同ファイル）:

| # | 行 | 処理 |
|---|---|---|
| 1 | :36-45 | flock 多重起動防止（`data/logs/daily_picks_wt.lock`） |
| 2 | :63-92 | `KEIRIN_DB_URL` 必須チェック（無ければ Discord 通知して中断） |
| 3 | :110 | `python -m src.cli.main collect-wt --date 昨日 --full-scan` |
| 4 | :115 | `scripts/notify_results_wt.py 昨日`（前日成績通知） |
| 5 | :137-139 | T-2〜T-4 の結果バックフィル（`collect-wt --full-scan` + 採点） |
| 6 | :150 | **`collect-wt --date 当日 --full-scan`（当日データ収集）** |
| 7 | :161-178 | `check_race_point_sanity.py` → 異常なら5分待って再収集 |
| 8 | :192-209 | `check_line_readiness.py` → 同上 |
| 9 | :215 | `snapshot_morning_odds_wt.py`（朝オッズ退避） |
| 10 | :225 | `wave-picks-wt --min-gap12 0.07 --include-7plus`（**旧ランクの候補生成。売らないが Web 用に残っている**） |
| 11 | :270-321 | 7H1/7H2/9H1/7T1/7T3 の候補生成 — **すべてコメントアウト済み（2026-09-11）** |
| 12 | :340 | `reselect_7s_evening.py`（7S 日次上限・旧ランク） |
| 13 | :348 | `write_candidates_wt.py`（picks_history へ候補書き込み） |
| 14 | **:395** | **`bash scripts/type_lab_daily.sh` ← ここが本番の商品** |
| 15 | :402- | 前日処理（06:30 が落ちたときの保険） |
| 16 | :420 | `migrate_sqlite_to_pg.py`（SQLite→PG 同期） |

### 1.1 型ラボ本体 `scripts/type_lab_daily.sh`

```
:37  build_type_lab_picks.py --mode live --date TODAY              （7車・mode='live'）
:42  build_type_lab_picks.py --mode live --date TODAY --n-entries 9（9車・mode='live9'／失敗しても続行）
:55  build_race_shapes.py --date TODAY                             （表示専用の型判定・5/6/8車ぶん）
:59  netkeirin_submit_type_lab.py TODAY morning                    （入稿。自信ありの選定はこの中）
:74  settle_type_lab_picks.py --date YESTERDAY
:76  settle_type_lab_picks.py --date TODAY
```

### 1.2 データ収集

- `src/cli/main.py:942 collect-wt` → `src/scraper/pipeline_wt.py`（winticket の
  `PRELOADED_STATE` JSON）。
- 書き込みは `pipeline_wt.py:193 _write_race()`:
  - `wt_races`（:194 `INSERT OR REPLACE`）
  - `wt_entries`（:214 `INSERT OR REPLACE` — **race_point / prediction_mark /
    comment / first_rate,second_rate,third_rate / ex_*_pct / line_* を毎回上書き**）
  - 現在の出走表に無い `frame_no` の行を DELETE（:246-252・欠車ガード）
  - `wt_odds`（:265 `INSERT OR REPLACE`）
- 🔴 `collect-wt` は**レース後にも何度も走る**（06:30 / 07:00 の T-2〜T-4 /
  `*/15` の intraday）。リーク査読の中心（§3）。

### 1.3 モデル推論（live）

`scripts/build_type_lab_picks.py:224 predict_p3_pw()`:
```python
feats = build_features_wt(load_raw_data_wt(min_date=day, max_date=day))
X     = prepare_X(feats)
p3    = load_model("lgbm_wt_eval").predict_proba(X)[:, 1]   # 3着内率
pw    = load_model("lgbm_wt_win").predict_proba(X)[:, 1]    # 1着率
```
- 🔴 **後段較正（`src/p3_calibration.py` → `backend/src/services/keirin_p3_calibration.py`）は
  型ラボ経路では一切使われない。** grep で呼び出し元は `src/cli/main.py`(旧ランク)・
  `src/strategy_wt.py`・`src/race_gate_7c.py`・実験スクリプトだけ。
  `src/type_lab.py:53-71` のコメントが「生の p3 のまま使う」と明示していて、
  コードもそのとおり（`AXIS_SUM_FIRM=1.44` は生の p3 と比較）。
- `load_raw_data_wt` は当日1日ぶんしか SELECT しないが、rolling 特徴は
  `feature_wt.py:617 add_rolling_features_wt()` が**独自 SQL で履歴を直接引き直す**
  （:633-649）ので当日だけでも壊れない。

### 1.4 予測オッズ（三連単の板）

`build_type_lab_picks.py:296`:
```python
board = odds_tf.predict_board(sorted(p3[rk]), p3[rk], pw[rk], {c: ent[c]["meta"] for c in ent})
```
- `src/odds_prediction_tf.py:335 predict_board()` — LightGBM で `log10(オッズ)` を
  予測 → `10**` → レース内で `Σ(1/オッズ)` を `target_sum(n_car)` へ再スケール。
- 入力 `meta` は `build_type_lab_picks.py:415 _META_COLS` ＝
  `race_point, line_group, line_size, line_pos, is_line_leader, player_class,
  style, first_rate, second_rate, third_rate` ＋ `prediction_mark`(mark)。
- 三連複の予測オッズは**三連単板から畳む**（:148 `_fold_to_trio`、`1/Σ_perm(1/PO)`）。
- 買い目の並べ替え用確率は `_pl_board`（位置別合成 PL ＋ 同ライン隣接ボーナス
  λ=2.0/μ=1.5・正本 `strategy_wt.rank_7t3_blend_probs`）と
  `_pl_board_order`（`rank_7t3_order_swap_probs`）。**予測オッズとは別系統の確率**。

### 1.5 型判定（A〜F）

`src/type_lab.py:539 race_shape()`。入力は p3（必須）・line_group・line_pos・style・
race_point・ex_left_behind_pct・day_index・pw（任意）。

```
order    = p3 降順の車番
axis_sum = p3[order0] + p3[order1]
gap      = (p3[3位]+p3[4位])/2 − mean(p3[5,6,7位])          ← order[2:] が5要素未満なら 0.0
arare(s) = (ライン人数 2→+1 / 4以上→−1)
           + (先頭の遅れ率 >= 11.0 → −1 / 未満 → +1)
           + (先頭の脚質=="追" → +2)
           + (day_index − 2)
           + (番手の競走得点 > 先頭の競走得点 → +1)
firm     = axis_sum >= AXIS_SUM_FIRM(1.44)
label    = firm ? (s<=-1:"A", s==0:"B", else "C") : (s<=-1:"D", s==0:"E", else "F")
pw_ent   = 1着率のエントロピー（`win_entropy`・型A の売り分けだけが使う）
```
⚠️ `gap` は `len(order[2:]) >= 5` が条件。7車（order 7要素・others 5要素）と
9車では計算されるが、**6車以下では常に 0.0** になる（`build_race_shapes.py` が
5/6/8車の表示用の型も作るので、6車の `gap` は情報を持たない）。

### 1.6 プラン（買い方）— **現在有効なもの**

`src/type_lab.PLANS` を実行してダンプ（実測）。**生成**は `plans_for()`、
**入稿**は `sell_plans_for()`。7車では段（T_*）も生成されるが売らない。

| plan_key | 型 | 券種 | 構造 | 帯/点数 | 配分 | いま売るか |
|---|---|---|---|---|---|---|
| `A_hit` | A | 三連単 | prob_top 3点 | — | conf(床2.0倍) | ✅ 型A の既定 |
| `A_trio` | A | 三連複 | axis2_flow 2点 | — | dutch | ✅ trio_ok のとき |
| `A_ana` | A | 三連単 | bust_top 5点（軸1を全排除） | — | dutch | ✅ pw_ent>=1.4076 |
| `A_pay` | A | 三連単 | axis1_second2 | — | conf | ❌ 比較台のみ |
| `B_hit` | B | 三連単 | prob_top 8点 σ<=1/3 | — | conf(床2.0) | ✅ |
| `C_hit` | C | 三連単 | prob_top 12点 | 15倍以上・**τ適応**・underband 5.0 | conf(床2.0) | ✅ |
| `D_hit` | D | 三連複 | axis2_drop_fav 3点（最人気1点を外す） | — | conf(床2.0) | ✅ |
| `E_hit` | E | 三連単 | prob_top 14点 | 30倍以上・**τ適応**(床35,000円・最大20点) | conf(床2.0) | ✅ |
| `F_hit` | F | 三連単 | prob_top 12点 | 5倍以上 | conf(床2.0) | ✅ 型F の既定（7車の非決勝） |
| `F_pay` | F | 三連単 | axis1_second2 4点 | — | conf | ✅ 決勝/チャレンジ決勝（7車・9車） |
| `F_line` | F | 三連複 | line_axis2_flow（最強ラインの上位2車軸） | σ<=0.5・最低3点 | dutch | ✅ **9車の非決勝のみ** |
| `F_sign` | F | 三連単 | signboard 計画15万・<=600倍 | — | dutch | ✅ 7車 ∧ 決勝系/準決勝系 |
| `{B,C,D}_sign` | B/C/D | 三連単 | signboard 計画15万 | — | dutch | ✅ **高額枠**（origin=`highpay_fill`） |
| `{B,C,D}_big` | B/C/D | 三連単 | signboard 計画40万・**軸1全排除** | — | dutch | ✅ 高額枠の2・4本目 |
| `{A,E}_sign/_big` | A/E | | signboard | | | ❌ 生成のみ（`HIGHPAY_TYPES=(B,C,D)`） |
| `T_firm/T_mid/T_axis/T_upset` | 段 | 三連単 | tier_firm / tier_axis / tier_upset | 計画2.2万/2.2万/10万 | dutch | ❌ **`TIER_SELL_ENABLED=False`（停止中）** |

実行ダンプ（`.venv/bin/python` で `src.type_lab` を import して確認）:
- `TIER_SELL_ENABLED = False`
- `UPPER_BANDS = ()` … **上帯（押さえの重ね買い）は無効**（2026-09-04 に採用→同日切り戻し）
- `OSAE_PLANS = frozenset()` … 押さえ目も無効
- `rule_version(7) = 5de3d5e878c1` / `rule_version(9) = 819c8fdb70b8`

**DB 実測（`keirin.netkeirin_submissions`・`left(race_key,8) >= '20260901'`）**:

| rank_key | origin | 件数 | 初出〜最終 |
|---|---|---|---|
| F_hit 192 / C_hit 171 / B_hit 127 / E_hit 105 / A_hit 101 | rank | | 09-01〜09-20 |
| F_sign 87 / D_hit 64 / A_ana 46 / A_trio 24 / F_pay 2 | rank | | 09-01〜09-20 |
| F_line 36 | rank | | 09-06〜09-20 |
| C_sign 24 / B_sign 22 / D_sign 15 / C_big 11 / B_big 10 / D_big 7 | **highpay_fill** | | 09-06〜09-20 |
| T_firm 28 / T_upset 28 / T_mid 19 / T_axis 1 | rank | | **09-15 のみ** |

＝ 段（T_*）は 2026-09-15 の1日だけ売られ、以降停止。これはコードの
`TIER_SELL_ENABLED=False` と整合。

**`keirin.netkeirin_settings` 実測**: 旧ランク 7S/7A/7B/7C/7SS/7M1/7H1/7H2/7T1/7T3/
9A/9C/9S/9SS/9H1/S1 は **全て `enabled=false`**。有効なのは型ラボの plan_key のみ。
`_global.require_approval = true`（**承認制 ON**）。

### 1.7 入稿ゲート（4段）

`scripts/netkeirin_submit_type_lab.py:812 run()` の `_reject()`（:456-…）が
上から順に判定。判定できないものは通す方針。

| 順 | 条件 | 実装 | 記録コード |
|---|---|---|---|
| 0 | `netkeirin_settings.enabled` | `_is_enabled` | (silent) |
| 0 | 同じ (race_key, plan) を既に入稿 | `_already_submitted` | (silent) |
| 0 | 他ランク / 型ラボの別プランが取得済み（1レース1商品） | `taken` / `taken_by_type_lab` | (silent) |
| 1 | 発走15分前を過ぎた | `_load_closed_races` | `closed` |
| 2 | **並び予想・AI印が未公開** | `src/entry_health.missing_market_inputs` | `missing_lineup` |
| 3 | **軸信頼ゲート**（プラン内 axis_sum の下位1/5） | `backend/src/services/keirin_type_lab_gate.passes_axis_gate` | `axis_gate` |
| 4 | **入稿ゲート**: 平均想定払戻 > 20,000円 ∧ 全点の予測オッズ >= 2.0倍 | `_gate_reason`(:406) / `src/stake_allocation.py:286,453` | `gate_mean_payout` / `gate_point_odds` |
| 5 | **日次上限**: 枠外を除いた判定対象 × 0.5 | `DAILY_CAP_RACE_FRACTION=0.5` | `daily_cap` |

軸信頼ゲートの閾値（`keirin_type_lab_gate.AXIS_GATE_MIN`・**7車のみ**・9車は素通し）:
`A_hit 1.596 / A_trio 1.528 / B_hit 1.539 / C_hit 1.500 / D_hit 1.304 /
E_hit 1.284 / F_hit 1.246 / F_sign 1.267`。
`A_ana`・`A_pay`・`F_pay`・`F_line`・`{B,C,D}_sign/_big` は**意図的に exempt**。

日次上限の免除: 9車全部 / `race_type` に「決勝」を含む（準決勝も含む・意図的） /
`cup_grade >= 3` / `cap_free_plans()`（段が停止中なので現在は空集合）。
残す順は `cap_priority = (2×axis_priority + rp_sd_priority)/3`。

### 1.8 高額枠（`origin='highpay_fill'`）

`run()` 内 `_try_highpay`（:1055）。**供給源は2つ**:
① 日次上限で捨てる行（:1136 付近） ② **軸信頼ゲートで落ちた行**（:1129）。
1日 `HIGHPAY_SLOTS_PER_DAY = 5` 本（2026-09-19 に 10 → 5 へ戻した）。
2・4本目が `{型}_big`（計画40万・軸1排除）、他は `{型}_sign`（計画15万）。
型は B/C/D のみ、7車のみ。**上限の枠を消費しない＝既存商品を減らさない**。

### 1.9 「自信あり」

`scripts/netkeirin_submit_type_lab.py:482 _choose_confident()` →
`src/confident_pick.py:247 type_lab_confident_score()`（段が停止中なので必ずこちら）。
- 候補: 発走 JST < 18:00 ∧ **合成オッズ >= 2.5倍**（上帯は除外して判定）
- 順位: **Σp（買い目の確率の和＝的中確率）最大**
- **朝の回だけ**・入稿の**前**に選ぶ（アイコンは入稿の瞬間にしか渡せないため）
- 高額枠は母集団に入らない（`_plan_attempts` が通常商品の行だけを渡す）

### 1.10 賭け金配分

`src/type_lab.py:1868 allocate()`。予算 `BUDGET=10,000` 円・`UNIT=100` 円。
- `dutch`: 賭け金 ∝ 1/予測オッズ（どの点でも払戻が揃う）
- `conf`: 各点に床 `予算 × floor_mult ÷ 予測オッズ` を置き、残りを確率比例で配る
  （hit 系6プランは `floor_mult=MIN_PAYOUT_MULT=2.0`＝当たれば最低2万円）
- **賭け金0円の点は買い目から落として配り直す**（:1891-1901）
- 組めなければ `alloc_fallback`（床 1.3倍）へ退避

配分後に順に: `apply_line_swap`（ライン決着への差し替え・B/C/E/F_hit）→
`apply_order_swap`（並べ替え・B_hit/F_hit）→ `apply_osae`（現在 OSAE_PLANS 空で no-op）
→ `add_upper_band`（現在 UPPER_BANDS 空で no-op）。

### 1.11 baseline の目視確認（実データ1件）

`20260919_21_12 / F_hit / mode=live`（実際に売って的中した行）:
- `type_label=F, axis_sum=1.2595, arare=2, gap=0.1489, n_legs=12, rule_version=5de3d5e878c1`
- 賭け金合計 = 900+1900+600+500+400+1100+1200+1000+600+800+600+400 = **10,000円** ✅
- `pred_mean_payout = 25,345.9` … 自分で再計算 Σ(stake×pred_odds)/12 = **25,347** ✅
- `pred_min_payout = 21,604.6` … 最小点 600×36.01 = 21,606 ✅
- ゲート: 平均 25,345.9 > 20,000 ✅ / 最小予測オッズ 11.78 >= 2.0 ✅ /
  axis_sum 1.2595 >= F_hit の 1.246 ✅
- 決着 `7-3-4`（賭け金500円の点）→ `payout=65,000`＝確定 130.0倍（予測は 51.64倍）

＝ 生成・ゲート・記録は内部整合している。


---

## 4. 学習と配信のコード経路（train/serve skew の構造的リスク）

### 4.1 同じ関数を通っているか → **通っている**

| 経路 | 特徴量生成 | file:line |
|---|---|---|
| 学習 | `build_features_wt(load_raw_data_wt(min_date=from, max_date=load_max))` → `prepare_X` | `src/cli/main.py:1103,1108,1223`（`train-wt`） |
| 配信（型ラボ） | `build_features_wt(load_raw_data_wt(min_date=day, max_date=day))` → `prepare_X` | `scripts/build_type_lab_picks.py:238-241` |
| 配信（旧ランク） | 同上 | `src/cli/main.py:1462,1468-1469` |
| バックテスト | `prepare_X` | `src/evaluation/backtest_wt.py:79` |

＝ **特徴量の実装は1本**（別実装は無い）。地方 v13→v14 で起きた型の skew は構造的に起きにくい。

### 4.2 それでも残る差（**読み込み範囲が違うことから来る**）

`build_features_wt` の中に**バッチ全体の統計**が2つある:

| file:line | 内容 | 学習時 | 配信時 |
|---|---|---|---|
| `feature_wt.py:167-168` | `med_rp = df["race_point"].median()` で `race_point==0/NaN` を補完 | 数年ぶん（約70万行）の中央値 | **その日だけ**（数百行）の中央値 |
| `feature_wt.py:179-180` | `med_term = df["term"].median()` で `period_norm` を補完 | 同上 | 同上 |

`race_point == 0.0` は「ガールズ・新人戦など未点数」＝ **全車 0 のレースでは
レース内中央値も NaN になりグローバル中央値へ落ちる**（:161-168 のコメントが
その設計を明記）。したがってこの2つは **配信時と学習時で違う値が入りうる**。
影響範囲は「未点数の行」に限られるが、`race_point` は `score_z` / `score_rank` /
`line_rp_*` の入力なので、該当レースでは下流が丸ごとずれる。**重大度: 中**
（定量は未実施＝限界）。

### 4.3 型判定・予測オッズは「学習を通らない」入力を直に読む

- `race_shape()`（`src/type_lab.py:539`）は `race_point` と `ex_left_behind_pct`
  を**生のまま**閾値（`BEHIND_MID = 11.0`）と比べる。これは
  `FEATURE_COLS_WT` に入っていない列＝モデルの学習/検証を一度も通っていない入力。
  🔴 `ex_*_pct` のうち `ex_spurt_pct` / `ex_thrust_pct` は **2026-07-31 に
  「開催中に値が更新される（train/serve skew）」ことを理由に `FEATURE_COLS_WT`
  から除外された**（`docs/prediction-factors.md:12`）。**同じ family の
  `ex_left_behind_pct` は除外されていないどころか、型判定の `arare` の
  加算項として直接使われている。** 重大度: 中〜高（§3 の実測結果を参照）。
- `predicted odds` の入力 `meta`（`build_type_lab_picks.py:415 _META_COLS` + `mark`）も
  同様に `race_point` / `prediction_mark` / `first_rate` 等の生値。

### 4.4 節内成績特徴（`cup_*`・2026-08-20 追加）— **point-in-time は成立していた（実測）**

`feature_wt.py:931 add_meeting_form_features_wt()` は
`sort_values(["cup_id","player_id","_day"])` → `groupby.transform(x.shift().expanding().mean())`。
`shift()` が当該行を除くので設計としては正しいが、**同一 `(cup_id, player_id, day_index)` に
2行あると順序が非決定になり同日の別レースが履歴に入りうる**。

→ **実測して否定した。** 2026-01-01〜2026-08-31 の 130,463 グループすべてが
`n = 1`（同一開催・同一選手・同一日目に2レース以上は **0件**）。
`wt_races` の `cup_id` / `day_index` に NULL も 0件（18,382行）。
＝ この特徴のリーク経路は**実在しない**。


---

## 6. ドキュメントと現物の食い違い（行番号つき）

### 6.1 🔴🔴 最大: `keirin/CLAUDE.md` の中心セクションが**丸ごと死んだ体系**を説明している

| 文書 | 行 | 記述 | 現物 |
|---|---|---|---|
| `keirin/CLAUDE.md` | **305** | `## 現行ランク体系（2026-08-17〜・7S/7B/7C/7M1/7H1/7H2/7T1/9C/9H1 の9ペーパーランク）` — **ここから 860行目まで約550行** | 🔴 **9ランクすべて `netkeirin_settings.enabled = false`**（DB実測）。うち 7H1/7H2/9H1/7T1/7T3 は **候補生成すら 2026-09-11 にコメントアウト**（`scripts/daily_picks_wt.sh:270-321`）。現行の商品は**型ラボ**（`src/type_lab.py`） |
| `keirin/CLAUDE.md` | 613 | 入稿優先順位 `7H2 > 7T1 > 7T3 > 7S > 7B > 7C > 7H1 > 7M1`・`RANK_CONFIGS` の定義順が正本 | 🔴 **1件も入稿されない**。現行の優先順位は「型は排他なので1レース1商品」＋日次上限の `cap_priority`（`backend/src/services/keirin_type_lab_gate.py:545`） |
| `keirin/CLAUDE.md` | — | 型ラボへの言及は**全文で4か所だけ**（:16 / :299 / :1329 / :1335）で、いずれも脇の注記 | 本番の商品定義・ゲート・上限・自信あり・高額枠のすべてが型ラボ側にある |
| `keirin/CLAUDE.md` | 71 | `scripts/daily_picks_wt.sh  # 日次運用（cron 8:00）` | 🔴 VPS crontab 実測は **07:00**（スクリプト先頭のコメント :2 は 07:00 に是正済みで、キーファイル表だけ取り残されている） |
| `keirin/CLAUDE.md` | 1016-1057 | 「3着内率の後段較正」＝ 7C 1.44 / 9C 1.30 のゲートに効く | 🔴 **型ラボは較正を使わない**（`src/type_lab.py:53-71` が「生の p3 のまま」と明記し実装もそのとおり。grep で型ラボ経路からの呼び出し 0 件）。いま較正が生きているのは **Web の `confidence_pct` 表示だけ**（`backend/src/api/keirin_router.py:1438,3334`） |

`keirin/docs/RECOMMENDATION.md` は 2026-09-16 更新で**現物と一致している**
（`TIER_SELL_ENABLED=False`・段の停止・高額枠・自信ありの規則まで合う）。
＝ **CLAUDE.md と RECOMMENDATION.md が二重管理になり、CLAUDE.md だけが腐っている。**

### 6.2 特徴量数の記述が**3種類あって全部違う**

実測: `len(FEATURE_COLS_WT) == 70`（`.venv/bin/python` で import して確認）。

| 文書 | 行 | 記述 | 実測 |
|---|---|---|---|
| `keirin/CLAUDE.md` | 53 | `FEATURE_COLS_WT（60特徴・rolling統合…48→60）` | **70** |
| `keirin/docs/prediction-factors.md` | 4 | 本番モデル `lgbm_wt` / **46特徴量** | **70** |
| `keirin/docs/prediction-factors.md` | 130 | `### 2-1. 特徴量一覧（FEATURE_COLS_WT / 60特徴量）` | **70** |
| `keirin/docs/prediction-factors.md` | 416, 495 | 「全60特徴中2位の重要度」 | 母数が違う |
| `keirin/docs/system-architecture.md` | 17,35,96,145,222 | `lgbm_wt（48特徴）` | **70** |

git 履歴（`git log -- keirin/src/preprocessing/feature_wt.py`）:
`882d7285`（66特徴へ）→ `2657316f` "節内成績4特徴をモデルへ配線する（66→70特徴）" (#561)。
**`CLAUDE.md:3-18` が「FEATURE_COLS に特徴量を追加・削除したら `docs/prediction-factors.md`
を必ず更新する」と自分で定めているルールが、少なくとも2回連続で守られていない。**

### 6.3 cron の写しが現物でない

`keirin/data/cron_new_20260613.txt` / `cron_backup_*.txt` は 2026-06 の写しで、
keirin のパスが `~/GitHub/keirin`（**統合前**）のまま。型ラボの cron（07:00 /
13:05 / 18:05 / 採点 `5,20,35,50`）は1行も無い。
→ **cron を知りたいときにこのファイルを読むと必ず間違える。**


---

## 7. 実運用ログによる経路の裏取り（VPS `data/logs/cron.log`・2026-09-20 当日）

```
[07:00:01] === winticket日次処理開始 2026-09-20 ===
[07:00:01] 当日(2026-09-20) winticketデータ収集（全会場走査）...
[07:03:50] 予想生成（winticket・7+車専用 gami≥5倍+gap12≥0.07）...   ← wave-picks-wt（旧ランク候補）
[type_lab] 07:11:00 build live 2026-09-20
[type_lab] 07:18:07 build live9 2026-09-20
[type_lab] 07:24:54 build shapes 2026-09-20
[type_lab] 07:31:46 submit morning 2026-09-20
[type_lab_submit] 上限 22件（枠外を除いた判定対象 45レース × 0.5）＋ 枠外 22件は上限の対象外
[type_lab_submit] 自信あり → 富山10R(C_hit) Σp=0.261（対象 44件 / Σp算出 10件）
[type_lab_submit] 高額枠 残り 5本（本日 0本 出済み・候補 20レース）
[type_lab_submit] 2026-09-20 morning: 入稿 49件（うち高額枠 5件）
                  見送り {'axis_gate': 8, 'daily_cap': 8, 'gate_mean_payout': 5}
[type_lab] 07:31:58 settle 2026-09-19 / settle 2026-09-20
[07:32:01] === winticket日次処理完了 ===
```

- DB の `netkeirin_submissions`（20260920）＝ rank 44 + highpay_fill 5 = **49件**で一致。
- `submission_skips`（20260920）＝ axis_gate 8 / daily_cap 8 / gate_mean_payout 5 で一致。
- **「自信あり」の尺度が 09-19 の `EV=1.129` から 09-20 は `Σp=0.261` へ変わっている**
  ＝ `f5c435f6`（2026-09-19 08:40 マージ「自信ありを合成2.5倍以上×Σp最大へ戻す」）が
  当日の朝から効いていることの実測。
- **旧ランクの `wave-picks-wt` は今も毎朝約7分走っている**（07:03:50→07:11:00）。
  売らない候補を作るためだけに当日の入稿を約7分遅らせている（2026-09-11 に
  他5本を止めた理由とまったく同じ構図が `wave-picks-wt` には残っている）。


---

## 2. 本番モデルの実体

（詳細は `sub_models/REPORT_models.md`。以下は要点＋統括者が独立に再確認した部分）

### 2.1 朝に実際に読まれるモデル

| 役割 | ファイル | 読む場所 | 学習日 |
|---|---|---|---|
| **p3（3着内率）— 売る買い目・型判定** | `data/models/lgbm_wt_eval.pkl` | `scripts/build_type_lab_picks.py:224,245` | 2026-09-13 |
| **pw（1着率）— 売る買い目** | `data/models/lgbm_wt_win.pkl` | 同 `:225,246` | 2026-09-14 |
| p3 — Web 指数 / 旧ランク候補 / 三連複予測オッズの入力 | `lgbm_wt.pkl` | `src/cli/main.py:1287`（既定）, `:1455` | 2026-09-14 |
| 2着内（表示専用） | `lgbm_wt_top2.pkl` | `src/cli/main.py:1486` | 2026-09-14 |
| 大敗（旧ランクの軸2選定） | `lgbm_wt_bad.pkl` | `src/cli/main.py:1499` | **2026-09-10（週次の対象外）** |
| 三連単の予測オッズ | `odds_tf_n7.txt` / `odds_tf_n9.txt` | `src/odds_prediction_tf.py:258-263` | **2026-08-12 / 08-28** |
| 三連複の予測オッズ | `odds_trio_n7.txt` / `n9.txt` | `src/odds_prediction.py:330-337` | 2026-08-20 |

**死んだ資産（VPS へ配布は続くが朝は読まれない）**: `lgbm_wt_favbust` /
`lgbm_upset_screen`（唯一の呼び出し元 `build_7h1_candidates.py` / `build_9h1_candidates.py`
が `daily_picks_wt.sh:278,297` でコメントアウト済み）、`lgbm_wt_train_only`、
`lgbm_wt_win_eval` / `lgbm_wt_top2_eval`（AUC ゲート監視のみ）、`lgbm_v*` / `lgbm_pair` /
`baseline`、月次 vintage 324本（backfill・検証専用。`run_live` に vintage を渡す経路は無い）。

VPS と Mac のモデルは **mtime・サイズが完全一致**（vintage も 324 対 324）。
＝ 週次再学習（Mac 日曜 23:30）→ `sync_models_to_vps.sh` の配布は届いている。

### 2.2 🔴🔴 売り物の p3 は「評価専用」と文書化されたモデルで作られている（統括者が独立に再確認）

`data/models/*.meta.json` を直接読んだ実測:

| モデル | `full_refit` | `test_from` | `fit_rows` | `trained_at` |
|---|---|---|---|---|
| `lgbm_wt` | **true** | null | **735,256** | 2026-09-14T00:39 |
| **`lgbm_wt_eval`** | **false** | **2026-06-15** | **686,152** | 2026-09-13T23:58 |
| `lgbm_wt_win` | **true** | null | 735,256 | 2026-09-14T01:46 |
| `lgbm_wt_win_eval` | false | 2026-06-15 | 686,152 | 2026-09-14T01:11 |

`scripts/build_type_lab_picks.py:254` の既定は `eval_model="lgbm_wt_eval"` /
`win_model="lgbm_wt_win"`。`main()`（:555）は `run_live(day)` を**引数なし**で呼ぶので
上書き経路も無い。つまり本番の商品は:

- **p3 = 直近90日を学習から外したモデル**（49,104 行＝約3か月ぶん少ない）
- **pw = 全期間 full-refit のモデル**

の**組み合わせ**で作られている。p3 は型判定（`axis_sum`）・軸の順序・
軸信頼ゲート・日次上限の優先順位のすべてを決める中心量なので、影響は商品全体に及ぶ。

一方 `docs/prediction-factors.md:4-5` は
「本番モデル = `lgbm_wt`」「**評価専用モデル** = `lgbm_wt_eval` / honest backtest 再構築用」
と書いている。**文書と現物が正面から食い違う。**
（`build_type_lab_picks.py:20-22` の docstring の表は `lgbm_wt_eval` を「本番モデル」と
呼んでおり、リポジトリ内でも呼称が割れている。）

サブエージェントが両モデルで実際に推論して比較（7車・2026-09-15 と 09-19 の 134R）:
`corr 0.9977〜0.9978 / MAE 0.0117〜0.0120`、**型判定（`axis_sum >= 1.44`）の不一致 5R = 3.7%
[CI 1.2–8.5%]・上位2車集合の不一致 7R = 5.2%**。
＝ 数値としては近いが、**閾値をまたぐレースが数%あり、そこでは商品が別物になる**。

### 2.3 特徴量

- `FEATURE_COLS_WT` = **70列**（実測。全 `*.meta.json` の `feature_count` も 70）。
- **オッズ由来の列は1本も無い**（70列を機械走査）。市場情報は AI印 `prediction_mark` の1列のみ。
- 内訳（統括者の分類）: 選手素性 8 / レース内正規化 5 / 枠・会場 6 / ライン構造・実力 15 /
  隊列 2 / レース種別 7 / 直近成績 rolling 12 / 競走得点トレンド 4 / B・S・上がり 4 /
  節内成績 4 / その他 3。
- 予測オッズモデル: `odds_tf` は **63列**（`load_meta()` が起動時に名前と順序の完全一致を照合）、
  `odds_trio` は 52列。

### 2.4 学習

- `scripts/weekly_retrain_wt.sh`（Mac cron 日曜23:30）: `--from 2022-12-01` **固定**、
  `TEST_FROM = 実行日 − 90日`。AUC ゲート（top3系 >= 0.75 / win系 >= 0.78・前回比 −0.02 超で昇格中止）
  → 全データ full-refit → vintage 退避。
- パラメータ `src/models/trainer.py:61-77`: `objective=binary` / `metric=auc` /
  `n_estimators=500` / `lr=0.05` / `num_leaves=31` / `colsample=0.8`。
  🔴 **CV と early stopping は AUC 表示のためだけで、最終モデルは
  `trainer.py:120-123` が全データ・early stopping なし・500木固定で再学習する。**
- 🔴 `src/keirin_protocol.py:69,78-82` の TRAIN/VAL/TEST（`TRAIN_FROM=2024-04-01` /
  `TEST_START=2026-07-01`）は **`weekly_retrain_wt.sh` と接続されていない**
  （import しているのは trainer / pair_model / upset_model と `exp_*` だけ）。
  本番は TEST 期間を含めて full-refit している。
- 🔴 `weekly_retrain_wt.sh` に `--target bad` の段が無い＝**`lgbm_wt_bad` は週次対象外**
  （最終学習 2026-09-10）。
- 🔴🔴 **予測オッズモデル（`odds_tf_n7` / `n9`）には自動再学習経路が存在しない**
  （`train_odds_prediction_tf.py` の呼び出し元 grep 0件）。`train_end = 2025-12-31` のまま
  **9か月**更新されていない。**入稿ゲートは `平均想定払戻 > 2万円`＝実質
  `Σ(1/予測オッズ) < 0.5` なので、予測オッズの水準がそのまま在庫（何件売るか）を決める。**
- 予測オッズの教師は **確定オッズ**（`wt_odds` の `collected_at DESC` 最終スナップ・
  `y = log10(三連単オッズ)`・L1 regression）。
  🔴 **学習時の p3/pw は walk-forward キャッシュ、配信時は `lgbm_wt_eval`/`lgbm_wt_win` の
  live 推論**＝入力源が違う（train/serve skew の未計測リスク）。


---

## 3. リーク（未来情報の混入）と train/serve skew の査読

（詳細は `sub_leak/REPORT_leak.md`。DB 実測はすべて日付で範囲を切って実行）

### 3.1 判定サマリ

| # | 項目 | 判定 | 重大度 |
|---|---|---|---|
| 1 | ローリング特徴の窓（`win_3m` 〜 `fh_best_rate_90` 等 12列 + `rp_*` 4列 + B/S 系） | **リーク無し（実証）** | — |
| 2 | `wt_entries` のプロフィール列がレース後の再取得で上書きされる懸念 | **skew 無し（実証）** | — |
| 3 | 🔴 **並び予想（`line_*`）と AI印（`prediction_mark`）の朝の欠測** | **skew を実証** | **高** |
| 4 | `race_point` の欠損補完中央値 `med_rp` の母集団差 | 実在（規模未計測） | 低〜中 |
| 5 | 🟠 2026-06 の `race_point` 局所破損 | 実在（原因未特定） | 中（データ品質） |
| 6 | オッズ由来の特徴 | **ゼロ（実証）** | — |
| 7 | 予測オッズモデルの入力 p3/pw が学習=walk-forward / 配信=本番 | 実在（規模未計測） | 中 |
| 8 | `p3_calibration` の窓（2025年） | live はリーク無し。ただし係数は in-sample 予測で推定 | 中（※型ラボは較正を使わないので**表示だけ**） |
| 9 | 🔴 本番4モデルが `full_refit:true`＝学習終端=現在 | live は honest / **過去の再構築・ROI 再集計は in-sample** | **高（評価の健全性）** |
| 10 | 学習と配信が同じ関数を通るか | **同一（実証）** | — |

### 3.2 (a) ローリング窓 — **リークは検出できなかった（実証）**

- 実装はすべて `rolling(w, closed="left")`（`feature_wt.py:657-660, 774-781, 911-914`）。
  pandas で実測: 同一タイムスタンプの行は窓から完全に除外される（`[NaN, 1.0, 1.0, 37.0]`）。
  `_dt` は `race_date`（時刻なし）なので**同日の自分の走りは必ず窓の外**。
- 行単位 `shift()` を使う `venue_wr` / `days_since` / `cup_*` も、
  **2026年の 141,017 (選手,日) 組で同日2走が 0 件**（SQL 実測）＝発火経路が無い。
  統括者側でも独立に `(cup_id, player_id, day_index)` の重複 0 件を確認（§4.4）。
- 履歴 H は `WHERE e.finish_order >= 1`（`:634-636`）＝未確定・欠車は集計から除外。

### 3.3 (b) `wt_entries` の再取得上書き — **仮説は実測で否定された**

機序は実在する（`pipeline_wt.py:214` の `INSERT OR REPLACE` が37列を全置換し、
`_get_collected_keys` は `finish_order >= 1` の行しかスキップしない）。
しかし**実際に動いているか**を測ると動いていない:

`LEAD(...) OVER (PARTITION BY cup_id, player_id ORDER BY race_date, race_no)` で
節内の隣接ペアを作り、値の変化率を測定:

| 列 | 2026-06 (n=10,248) | 2026-08 (n=10,620) |
|---|---|---|
| `first_rate` / `third_rate` / `s_count` / `style` | **0.0000** | — |
| `race_point` | 0.0702（※6月固有の破損・§3.5） | **0.0000**（平均絶対差も 0.0000） |
| `h_count` | 0.0006 | — |
| `b_count` | 決定実験: `Δb` を今走の `res_back` でクロス集計 → **9,774ペア全てで Δ=0** | — |

＝ 選手プロフィール列は**節単位のスナップショットで凍結**されており、
レース後の再取得でも値が変わらない。**「学習データは事後値」は誤り。**

### 3.4 🔴 (b') 実証された skew — 並び・印の朝の欠測

`src/entry_health.py:66 missing_market_inputs()` は「印が全車0」「ラインが1本」で
入稿を止め、`keirin.submission_skips` に `missing_lineup` として残す。

```
2026-08-01〜 の missing_lineup: 282件 / distinct race_key 234R
その 234R の「現在の」wt_entries: n_lines=0 の割合 0.0000 / 印なし率 0.4275
参考: 正常日（2026-09-14〜19）の印なし率 0.42〜0.45 / n_lines=0 は 0.0000
```
＝ **朝は欠測、学習時には必ず充足**。区別がつかないほど「後から埋まる」。

影響:
1. `line_size` / `line_pos` / `is_line_leader` / `n_lines` / `is_isolated` / `line_frac` /
   `line_rp_*` / `line_leader_*` / `formation_*` / `prediction_mark` の**20列前後**が
   配信時だけ退化値（全員 `line_size=1` / `n_lines=0` / `mark=0`）になる。
   モデルは学習でこの入力パターンをほぼ見ていない。
2. 🔴 **`missing_market_inputs` は `scripts/netkeirin_submit_wt.py`（入稿側）からしか
   呼ばれない。** `build_type_lab_picks.py` / `src/cli/main.py` の**生成側には掛かっていない**
   ＝ 壊れた入力で p3/pw・型判定・軸選定が作られ、そのまま `type_lab_picks` へ保存される。
   （`netkeirin_submit_type_lab.py` のコメント（:935 付近）もこの退化を認識しており、
   「欠測時の型は信用できない」と明記している。＝ **認識はあるが生成は止めていない**）
3. 規模: 2026-08-01〜09-20 の 3,912R 中 **234R = 5.98%**（入稿対象に限った数なので、
   生成側で退化していたレースはこれより多い可能性 — 未計測）。

### 3.5 🟠 2026-06 の `race_point` 局所破損

```
20260612_61_12 (S1/S2 9車): race_point = 5.4, 9.5, 24.7, 31.3, 35.9, 46, 48.4, 65.8, 72.4
20260613_61_09 (S1/S2 9車): race_point = 94.6 〜 104.19
同一選手 14264: 9.5 → 94.6 / 15725: 65.8 → 101.2（first_rate/third_rate は両日で同値）
```
月別「S級 ∧ `race_point < 30`」の割合: 01〜05月 0.156〜0.259% → **06月 2.294%（113件）**
→ 07〜09月 0.185〜0.247%。**6月だけ約10倍。**

現行コードに `UPDATE ... race_point` は無い（grep 実測）ので原因は過去のコードか収集元。
`race_point` は `score_rank` / `score_z` / `line_rp_*` / `rp_*_delta` の元なので、
**2026-06 を含む学習窓・検証窓は汚染されている。**
（`docs/prediction-factors.md:11` が 2026-06-18〜07-23 の「race_point が AI 予測確率で
上書きされていた」事故を記録している。日付が重なるので**その事故の残骸である可能性が高いが未確認**。）

### 3.6 (c) オッズ由来の特徴 — **ゼロ（実証）**

70列を機械走査して該当なし。市場情報は AI印 `prediction_mark` の1列だけ。
予測オッズモデルの教師は確定オッズ（`train_odds_prediction_tf.py:106-113`・
`ORDER BY collected_at DESC`）だが、「朝の入力から締切オッズを当てる」設計なので
ターゲットが事後値であること自体はリークではない。

🟠 ただし**入力側に skew がある**: 学習は walk-forward の p3/pw（`_load_wf_preds`）、
配信は `lgbm_wt_eval` / `lgbm_wt_win` の live 推論（`build_type_lab_picks.py:245-246,296`）。
配信側のほうが p3 が鋭いので予測オッズが偏りうる。**規模は未計測。**

### 3.7 (d) 欠測の扱い・経路の同一性 → §4 参照（同一関数）

### 3.8 (e) `p3_calibration`

- 正本は `backend/src/services/keirin_p3_calibration.py`、`FIT_WINDOW = 2025年`。
- 2026年の配信は窓の外なのでリークは無い。
- 🟠 係数は `full_refit:true` のモデルで 2025年をバックフィルした **in-sample 予測**に
  当てて推定されている＝較正ずれが過小に見え、係数が live に対して弱すぎる可能性。
- 🔴 **ただし型ラボ（＝売り物）は較正を使わない**ので、この影響範囲は
  **Web の `confidence_pct` 表示と、いま売っていない旧ランク**に限られる。


### 3.5b 🔴 統括者による追検証 — 2026-06-12 の `race_point` は**今も壊れたまま**

サブエージェントの指摘を受けて「上書きの署名（レース内 `race_point` 平均）」で
2025-01-01〜2026-09-19 の **S級・SA混合**レースを全走査した（`sql/q14.sql`）:

```
 race_date  | suspect_races (rp_avg < 70) | min_avg | max_avg
 2026-06-12 |              21             |  31.8   |  40.6
（他の日は1件も無い）
```

決定的な証拠（`sql/q12.sql`）— 同じ会場(43)の 2026-06-12 と 06-13:

| | n_entries | Σrace_point | avg |
|---|---|---|---|
| `20260612_43_01`〜`_12`（12R すべて） | 9 | **286.2〜365.6** | 31.8〜40.6 |
| `20260613_43_01`〜`_03` | 7 | 665.8〜684.3 | 95.1〜97.8 |
| `20260613_43_04`〜`_06` | 9 | 883.4〜907.1 | 98.2〜100.8 |

**Σ が車数に依らず約300** ＝ 3着内確率（%）の合計（P(top3) の総和 = 3）。
競走得点は選手1人あたり 90〜100 なので 9車なら Σ≈900 のはず。
＝ **`race_point` に `pred_top3_pct` が書き込まれている。**

対象: 2026-06-12 の **会場 43 と 61 の全24レース・210行**（venue 27/12/84/38/21 は正常）。

🔴 **`docs/prediction-factors.md:11,504` は「汚染期間 2026-06-18〜07-23 の生データ再取得…
完了済み」と書いている。** しかし
- 実際に壊れているのは **2026-06-12**（文書の窓の**外**・再取得の対象外だった）
- 再取得は `_get_collected_keys`（`pipeline_wt.py:168`）が `finish_order >= 1` の行を
  スキップするため、**結果が入った後のレースは二度と引き直されない**。
  → この24レースは今後どの経路でも自然には直らない。
- `scripts/check_race_point_sanity.py` は `daily_picks_wt.sh:161` で**当日ぶんにしか
  掛からない**（導入は 2026-07-23）ので遡及検知もしない。

影響: 210行 / 735,256行 = **0.03%** なので学習への影響はほぼ無い。
しかし **「汚染は解消済み」という記録が誤り**であり、同種の事故の検知網が
「当日だけ」しかないことを示している。重大度: 低（データ量）／中（記録の信頼性）。


---

## 5. 採点（settle）の現物仕様

（詳細は `sub_settle/REPORT_settle.md`。採点コードを import しない独立実装で再計算して検証済み）

### 5.1 経路

cron `5,20,35,50 8-23,0` → `scripts/type_lab_settle.sh:22-23`（**前日と当日の2本**）
→ `scripts/settle_type_lab_picks.py::main:116`。
冪等性は `_load_targets:47` の `settled_at IS NULL`（取り直しは `--redo` のみ）。

### 5.2 的中判定

- 当たり目は `src/result_top3.py` の `winning_trifectas` / `winning_trios` /
  `representative` を**実際に import**（`settle_type_lab_picks.py:36-38,149-150,185`）。
  🟢 **旧実装（`{着順:車番}` 辞書・`ORDER BY finish_order` の `[:3]`）は採点経路に残っていない。**
- `hit_trio()` / `hit_trifecta()` は**意図的に不使用**（1目しか返さないため）。
  同着で2目とも当たった行は**払戻を合算**している（実例 `20260915_13_09` 2着同着）。
- 独立再計算による検証（`mode='live'` 2026-08-27〜09-19・**6,601行**）:

| 検査 | 結果 |
|---|---|
| `hit` の不一致 | **0件** |
| `payout` の不一致（9月・確定オッズから再計算） | **0件** |
| 着順が無いのに採点済み | 0件 |
| 同着レースに掛かる全260行（live/paper/paper9） | hit・payout とも **0件不一致**・永久保留 0 |

### 5.3 払戻とROI

- 払戻は **`keirin.wt_odds`** から引く（`wt_race_payouts` は 2026-07-04 で更新停止・**未使用**）。
  `UNIQUE(race_key, bet_type, combination)` があるので `ORDER BY` 無しでも非決定性なし。
- 確定値であることを `wt_odds_snapshot` との突合で実測（14時スナップは 266点中259点が
  不一致 ↔ evening スナップは 329点全一致 ＝ `wt_odds` は最終値）。
- `budget` は全 **114,000行**で Σstake と一致・`stake=0` の leg は 0件
  ＝ **ROI の分母は実際の賭け金**。
- live 実測: n=6,601 / 素の的中 1,179（**17.86%**）/ **表示的中 1,121（16.98%）** /
  投資 66,010,000円 → 払戻 52,684,240円 / **ROI 79.81%**。

### 5.4 「表示的中」の定義

「払戻 > 賭け金合計（`budget`）」。🟠 **不等号が実装ごとに割れている**:

| 実装 | 定義 |
|---|---|
| API `backend/src/api/keirin_type_lab_router.py:507,623` | `payout > budget` |
| 夜レビュー `nightly_review_type_lab.py:860,1078` | `payout >= budget` |
| kiseki 側の正本 `net_hit` | `>=` |

該当する行は 11件（＝ちょうど収支トントンの行）。重大度: 低。

### 5.5 欠車・失格・中止

- `finish_order = 0` は欠車/失格＝着外（`feature_wt.py:118-120` の `between(1,3)` と整合）。
- 🔴 **欠車の返還が採点に反映されていない**（重大度: 中）。
  欠車選手は `wt_entries` から**行ごと削除**される（`pipeline_wt.py:246-252`）が、
  その車番を含む買い目の leg は**全損として計上**される。
  live で 9行 / 6レース・返還相当 **25,400円**。
  最悪例 `20260830_13_06 B_hit` は budget 10,000円 のうち **9,900円が返還対象なのに全損**。
  全体 ROI への影響は 0.04%。
- 中止レース（`wt_races.cancel = 1`）は未採点のまま残る。直近2か月の未採点 11行は
  **すべて中止レース**＝発走済みなのに取り残された live 行は **0件**。

### 5.6 その他の不整合

| # | 重大度 | 内容 |
|---|---|---|
| S2 | 中 | **的中しているのに確定オッズが無い行が永久保留**（paper 7行。例: 着順 1-7-4 を 8,500円で的中）。外れ行はオッズ不要で必ず採点されるので、**除外が的中側にだけ偏る**うえ警告も出ない |
| S4 | 低 | 丸めの二重管理: 型ラボは `round(stake × odds)`・正本は `payout_per_100`（10円未満切捨）。現データでは差 0件 |
| S5 | 低 | 採点窓が当日＋前日のみ。2日以上遅れた行を拾う自動経路が無い |


---

## 8. リスク一覧（重大度つき）

| # | 重大度 | 内容 | 証拠 |
|---|---|---|---|
| R1 | **高** | **売り物の p3 が「評価専用」と文書化されたモデル（`lgbm_wt_eval`・直近90日を学習から除外・fit_rows 686,152）で作られ、pw だけが full-refit（735,256）**。組み合わせが非対称。型判定が 3.7% のレースで `lgbm_wt` と食い違う | `build_type_lab_picks.py:254,555` / `*.meta.json` 実測 / `docs/prediction-factors.md:4-5` |
| R2 | **高** | **予測オッズモデル `odds_tf_n7/n9` に自動再学習が無い**（`train_end=2025-12-31`・9か月）。入稿ゲートは実質 `Σ(1/予測オッズ) < 0.5` なので、予測オッズの水準がそのまま**在庫（何件売るか）**を決める | `grep` で呼び出し元0件 / `odds_tf_meta.json` |
| R3 | **高** | **並び予想・AI印の朝の欠測が生成側で止められていない**。5.98% のレースで 20列前後が退化値になり、学習で見たことのない入力で p3/型判定が作られ DB へ保存される。入稿側だけが止めている | `entry_health.py:66` の呼び出し元は `netkeirin_submit_wt.py` のみ / `submission_skips` 234R / 234÷3,912 |
| R4 | **高** | **本番4モデルが `full_refit:true`＝学習終端=現在**。live は honest だが、**DB に入っている過去の予測値・そこから作った ROI/的中率の再集計はすべて in-sample** | `*.meta.json` 実測 |
| R5 | **高（文書）** | `keirin/CLAUDE.md` の中心550行（:305-860「現行ランク体系」）が**全ランク無効の死んだ体系**を説明。型ラボの記述は4か所の脇注だけ | `netkeirin_settings` 実測 / `daily_picks_wt.sh:270-321` |
| R6 | 中 | **欠車の返還が採点に反映されない**（live 9行・25,400円・ROI 影響 0.04%） | `sub_settle/REPORT_settle.md` |
| R7 | 中 | **的中しているのに確定オッズ欠落で永久保留**になる行がある。除外が的中側に偏り、警告も出ない | 同上（paper 7行） |
| R8 | 中 | **2026-06-12 の 24レース・210行で `race_point` が `pred_top3_pct` に上書きされたまま**。文書は「汚染は再取得で解消済み」と記録しているが窓が違い、再取得経路は結果確定済みレースを二度と引かない | `sql/q12.sql` / `q14.sql` / `docs/prediction-factors.md:11,504` |
| R9 | 中 | 予測オッズモデルの入力 p3/pw が**学習=walk-forward / 配信=本番**で出所が違う | `train_odds_prediction_tf.py:131-` vs `build_type_lab_picks.py:245-246,296` |
| R10 | 中 | `src/keirin_protocol.py` の TRAIN/VAL/TEST が `weekly_retrain_wt.sh` と**接続されていない**。本番は TEST 期間込みで full-refit | `keirin_protocol.py:69,78-82` / `weekly_retrain_wt.sh` |
| R11 | 中 | `race_shape` が `ex_left_behind_pct` を生のまま閾値判定に使う。同 family の `ex_spurt_pct`/`ex_thrust_pct` は 2026-07-31 に train/serve skew を理由に特徴量から除外された列 | `type_lab.py:539,564-566` / `docs/prediction-factors.md:12` |
| R12 | 中 | `lgbm_wt_bad` が週次再学習の対象外（最終 2026-09-10）。ただし消費者は旧ランクだけなので実害は不明 | `weekly_retrain_wt.sh` に `--target bad` 無し |
| R13 | 中（文書） | 特徴量数の記述が **46 / 48 / 60 の3種類**あり全部誤り（実測 **70**）。CLAUDE.md:3-18 の自己定義ルールが2回連続で守られていない | `len(FEATURE_COLS_WT)` 実測 / git log |
| R14 | 低〜中 | `med_rp` / `med_term` の欠損補完中央値が学習=全期間 / 配信=当日1日 | `feature_wt.py:167,179` |
| R15 | 低 | 「表示的中」の不等号が API `>` ↔ 夜レビュー/正本 `>=` で割れている（該当11行） | `keirin_type_lab_router.py:507,623` |
| R16 | 低 | 旧ランクの `wave-picks-wt` が毎朝**約7分**走り、売らない候補のために当日の入稿を遅らせている（他5本は同じ理由で 2026-09-11 に停止済み） | cron.log 07:03:50→07:11:00 |
| R17 | 低 | `data/cron_*.txt` が統合前パスの古い写し。型ラボの cron が1行も無い | ファイル実物 |
| R18 | 低 | `netkeirin_settings` で `T_firm/T_mid/T_axis/T_upset` は `enabled=true` のままだが、コード側 `TIER_SELL_ENABLED=False` が優先して売らない。**停止スイッチが2か所にある** | DB 実測 / `type_lab.py:988` |

