# 依頼A 項目3・4 — リーク / train-serve skew 査読レポート（2026-09-20）

監査対象: `keirin/src/preprocessing/feature_wt.py`（70特徴）・配信経路
`scripts/daily_picks_wt.sh` → `scripts/type_lab_daily.sh` → `scripts/build_type_lab_picks.py`。
git HEAD f5c435f6。**ドキュメントは証拠に使っていない**。以下はコード読解と DB 実測のみ。

---

## 0. 結論（先に）

| # | 判定 | 重大度 |
|---|---|---|
| 1 | ローリング特徴（win/top3/quin 3m6m, b/s_rate_90, fh_*, rp_ma90/180）の窓は **正しく `<` で切れている**。同日リークは構造的に不可能 | なし（実証） |
| 2 | `wt_entries` の選手プロフィール列（race_point / first_rate / third_rate / s,h,b_count / style）は **節(cup)内で完全に不変** ＝ レース後の再取得で値が変わらない | なし（実証） |
| 3 | 🔴 **`prediction_mark` / `line_*`(並び予想) は朝に欠測することがあり、学習データでは必ず埋まっている** ＝ 実証された train/serve skew | **高** |
| 4 | 🟠 `race_point` の欠損補完中央値 `med_rp` が学習=全期間 / 配信=当日1日 の母集団で計算される | 低（局所的） |
| 5 | 🟠 2026-06 に `race_point` の局所的破損（S級で得点<30 が 2.29%・平年 0.2%） | 中（データ品質） |
| 6 | FEATURE_COLS_WT に **オッズ由来の列は1本も無い**（70列を機械的に確認） | なし（実証） |
| 7 | 予測オッズモデルの学習ターゲットは **確定オッズ**（`ORDER BY collected_at DESC`）。設計どおりだが、入力の p3/pw は学習=walk-forward / 配信=本番モデル で出所が違う | 中（未計測） |
| 8 | `p3_calibration` の係数窓は **2025年**。2026年の live 配信は窓外＝リークなし。ただし **2025年を対象にした検証は in-sample** | 中 |
| 9 | 🔴 本番 `lgbm_wt_win` / `lgbm_wt_top2` / `lgbm_wt_bad` / `lgbm_wt` は **`full_refit: true`・学習終端=現在**。live は honest だが **過去の再構築・評価は全部 in-sample** | 高（評価の健全性） |
| 10 | 学習と配信は **同じ関数**（`load_raw_data_wt` → `build_features_wt` → `prepare_X`）を通る。別実装は無い | なし（実証） |

---

## 1. (a) ローリング窓の point-in-time 性

### 1.1 コード

- `add_rolling_features_wt` (`feature_wt.py:617-702`)
  - 履歴 H: `WHERE e.finish_order >= 1`（`:634-636`）
  - 窓: `H.set_index("_dt").groupby("player_id")[col].rolling(w, closed="left").mean()`（`:657-660`）
  - 当日・未確定行は merge で当たらないので as-of 分岐（`:675-697`）: `hp = H[(H["player_id"]==pid) & (H["_dt"] < dt)]`、90D/180D は `>= dt - 90D`。
- `add_rp_trend_features_wt` (`:710-800`): 同じく `rolling("90D"/"180D", closed="left")`（`:774-781`）、`rp_prev = ffill().shift(1)`。
- `add_sb_dyn_features_wt` (`:803-925`): `rolling("90D", closed="left")`（`:911-914`）。
- `add_meeting_form_features_wt` (`:931-1020`): `g[src].transform(lambda x: x.shift().expanding().mean())`（`:1012-1015`）。

### 1.2 実測1 — `closed="left"` は同一タイムスタンプ行を除外するか

```python
d = ["2026-01-01","2026-01-02","2026-01-02","2026-01-03"], v = [1,10,100,1000]
rolling("90D", closed="left").mean() -> [NaN, 1.0, 1.0, 37.0]
```
2件ある 01-02 の両方が 1.0（＝01-01 のみ）。**同日の自分も他の自分の走りも窓に入らない。**
`_dt` は `race_date`（時刻なし）なので、同日レースは必ず除外される。

### 1.3 実測2 — そもそも同日2走は存在しない

```sql
SELECT c, count(*) FROM (
  SELECT e.player_id, r.race_date, count(*) c
  FROM keirin.wt_entries e JOIN keirin.wt_races r USING(race_key)
  WHERE r.race_date BETWEEN '2026-01-01' AND '2026-09-19' GROUP BY 1,2) d
GROUP BY 1;
→ c=1 : 141,017 行のみ（c>=2 はゼロ）
```
**2026年 141,017 (選手,日) 中、同日2走は 0 件。** したがって
- `venue_wr` の `expanding().mean().shift(1)`（行単位シフト）
- `days_since` の `diff()`
- meeting_form の `shift()`（行単位）

はいずれも「同日を巻き込む」経路を持たない。**(a) について実質的なリークは検出できなかった。**

### 1.4 残る微差（実害なし）
`venue_wr` / `days_since` は merge パスが行単位 shift、as-of パスが `_dt < dt` で、
同日2走があれば挙動が割れる。1.3 のとおり現データでは発火しない。

---

## 2. (b) レース後に上書きされる列（train/serve skew）

### 2.1 機序（コード）
`src/scraper/pipeline_wt.py:193 _write_race()` は
`INSERT OR REPLACE INTO wt_entries (...37列...)` で行を**丸ごと**置換する（`:214-241`）。
`_get_collected_keys`（`:168-183`）は `finish_order >= 1` の行だけスキップするので、
結果が付くまで再取得され、**最終的に残るのは「結果を取れた時点」の値**である。
＝「上書きされうる」は事実。問題は「実際に変わっているか」。

### 2.2 実測 — 同一 (cup_id, player_id) の連続レース間で値が動くか

`LEAD(...) OVER (PARTITION BY r.cup_id, e.player_id ORDER BY r.race_date, r.race_no)` で
隣接ペアを作り、値が変わった割合を測った。

**2026-06（n=10,248 ペア）**

| 列 | 変化率 |
|---|---|
| first_rate | **0.0000** |
| third_rate | **0.0000** |
| s_count | **0.0000** |
| style | **0.0000** |
| h_count | 0.0006 |
| ex_spurt_pct | 0.0149 |
| race_point | 0.0702 ← 後述（6月固有の破損） |
| prediction_mark | 0.5899 ← レース固有なので当然 |
| line_size | 0.5118 ← レース固有なので当然 |

**2026-08（n=10,620 ペア）**: `race_point` 変化率 **0.0000**・平均絶対差 **0.0000**。

さらに `b_count` については決定実験を行った:
`delta_b = b_count(次走) − b_count(今走)` を、今走の `res_back`（B取得 0/1）と
次走の `res_back` でクロス集計 → **全 9,774 ペアで delta_b = 0**。
＝ `b_count` は自分のレース結果を取り込んでいない（節内で凍結）。

**結論**: `race_point` / `first_rate` / `second_rate` / `third_rate` / `s_count` / `h_count` /
`b_count` / `style` は **節単位のスナップショットで、レース後の再取得でも値が変わらない**。
「レース後に上書きされるから学習データは事後値」という懸念は、これらの列については
**実測で否定された**（ただし節をまたぐ更新はあり、それは正当な過去情報）。

### 2.3 🔴 実証された skew — 並び予想 / AI印の朝の欠測

`src/entry_health.py:66 missing_market_inputs()` は「印が全車0」「ラインが1本」を
欠測として入稿を止める。`keirin.submission_skips` にその記録が残る。

```sql
SELECT reason_code, count(*) FROM keirin.submission_skips
WHERE race_date >= '2026-08-01' GROUP BY 1;
→ missing_lineup 282 件（distinct race_key = 234）
```

その 234 レースの **現在の** wt_entries を見ると:

```sql
WITH s AS (SELECT DISTINCT race_key FROM keirin.submission_skips
           WHERE reason_code='missing_lineup' AND race_date >= '2026-08-01')
SELECT count(DISTINCT s.race_key), count(*),
       avg((e.n_lines IS NULL OR e.n_lines=0)::int),
       avg((e.prediction_mark IS NULL OR e.prediction_mark=0)::int)
FROM s JOIN keirin.wt_entries e USING(race_key);
→ 234R / 1,635行 / n_lines=0 の割合 0.0000 / 印なし率 0.4275
```

参考ベースライン（2026-09-14〜19 の確定日・n=522〜577/日）: 印なし率 0.42〜0.45、
`n_lines=0` は 0.0000。**つまり skip された 234 レースは、今では正常日と区別がつかない。**

→ **「朝は欠測 → 学習時には必ず充足」が実証された。** 影響:
1. `line_size` / `line_pos` / `is_line_leader` / `n_lines` / `is_isolated` / `line_frac` /
   `line_rp_*` / `line_leader_*` / `formation_*` / `prediction_mark` の合計 **20列前後**が、
   配信時のみ退化値（全員 line_size=1・n_lines=0・mark=0）になる。
   モデルは学習でこの入力パターンをほぼ見ていない。
2. `missing_market_inputs` が掛かるのは **`scripts/netkeirin_submit_wt.py` の入稿側だけ**
   （grep 実測: `src/entry_health` の呼び出し元は `netkeirin_submit_wt.py` のみ）。
   `build_type_lab_picks.py` / `src/cli/main.py` の**予測生成側には掛かっていない**。
   ＝ 壊れた入力での p3/pw・型判定・軸選定は**そのまま生成され DB に保存される**。
3. 規模: 2026-08-01〜09-20 の約50日で 234R。同期間の実測総レース数 3,912R（51日・`SELECT count(*) FROM keirin.wt_races`）に対し **5.98%**。
   （※ skip は入稿対象レースに限られるので、生成側で退化していたレースはこれより多い可能性がある — 未計測）

### 2.4 `race_point` の局所破損（2026-06）

2026-06 の同一節内で `race_point` が 7.02% 変化していた。実例:

```
20260612_61_12 (S1/S2 9車): race_point = 5.4, 9.5, 24.7, 31.3, 35.9, 46, 48.4, 65.8, 72.4
20260613_61_09 (S1/S2 9車): race_point = 94.6 〜 104.19
同一選手 松尾信太郎(14264): 9.5 → 94.6 / 青木瑞樹(15725): 65.8 → 101.2
（両レースで first_rate/third_rate は完全に同値＝プロフィールは正常）
```
S級選手の競走得点が 5.4 は実在しない。**この節の race_point は別の量で上書きされている。**

月別の「S級かつ race_point < 30」の割合:

| 月 | 2026-01 | 02 | 03 | 04 | 05 | **06** | 07 | 08 | 09 |
|---|---|---|---|---|---|---|---|---|---|
| % | 0.207 | 0.176 | 0.239 | 0.259 | 0.156 | **2.294** | 0.185 | 0.247 | 0.246 |

2026-06 だけ約10倍（113件）。**局所的な破損**であり恒常的リークではないが、
`race_point` は `score_rank` / `score_z` / `line_rp_*` / `line_leader_*` / `rp_*_delta` の
元になるので、6月を含む学習・検証窓は汚染されている。
現行コードに `UPDATE ... race_point` は存在しない（grep 実測: `pred_win_pct`/`pred_top2_pct`/
`pred_top3_pct`/`res_*` のみ）ので、原因は過去のコードか収集元と推測される（**未特定**）。

### 2.5 `med_rp` / `med_term` の母集団差（低）

`build_features_wt:166-168` の `med_rp = df["race_point"].median()` は
**読み込んだ DataFrame 全体の中央値**。
- 学習: `load_raw_data_wt(min_date=from_date, max_date=load_max)`（`main.py:1103`）＝ 数年分
- 配信: `load_raw_data_wt(min_date=target_date, max_date=target_date)`（`main.py:1462`,
  `build_type_lab_picks.py:241`）＝ **1日分**

同じ `race_point=0` の行が、学習では全期間中央値・配信では当日中央値で埋まる。
`med_term`（`:175`）も同型。補完対象は少数なので実害は限定的（**規模は未計測**）。

---

## 3. (c) オッズ由来特徴

- `FEATURE_COLS_WT` は 70 列。機械的に走査して `odds|pay|pop|ninki` を含む列は
  `rp_prev_delta`（"ev" の部分一致による誤検出）のみ。**オッズ由来の特徴はゼロ**。
- 予測オッズモデル `scripts/train_odds_prediction_tf.py`:
  - ターゲット: `SELECT DISTINCT ON (race_key, combination) ... FROM keirin.wt_odds
    WHERE bet_type='trifecta' ... ORDER BY race_key, combination, collected_at DESC`
    （`:106-113`）＝ **確定（最終）オッズ**。「朝の入力から締切オッズを当てる」設計なので
    ターゲットが事後値であること自体はリークではない。
  - 入力: `_load_entries`（`:78-101`）= wt_entries のプロフィール列（2.2 で安定を確認）＋
    `_load_wf_preds`（`:131-`）= **walk-forward の p3/pw**。
  - 🟠 ただし配信時は本番モデル（full_refit）の p3/pw が入る
    （`build_type_lab_picks.py:296` → `odds_tf.predict_board(..., p3[rk], pw[rk], ...)`、
    その p3/pw は `:245-246` の `load_model(eval_model/win_model).predict_proba`）。
    **学習入力 = walk-forward 予測 / 配信入力 = 本番（より強い）予測** という
    分布差がある。方向としては配信のほうが p3 が鋭くなるので予測オッズが
    偏りうる。**規模は未計測**。

---

## 4. (d) 学習パスと配信パスの同一性

| 段 | 学習 | 配信 |
|---|---|---|
| 生データ | `load_raw_data_wt` (`main.py:1103`) | `load_raw_data_wt` (`main.py:1462` / `build_type_lab_picks.py:241`) |
| 特徴量 | `build_features_wt` (`main.py:1108`) | `build_features_wt` (`main.py:1468` / `build_type_lab_picks.py:241`) |
| 行列化 | `prepare_X` (`main.py:1223`) | `prepare_X` (`main.py:1469` / `build_type_lab_picks.py:244`) |

**同じ関数**。別実装は `src/` 内に存在しない（grep 実測、呼び出し元は上記＋
`src/evaluation/backtest_wt.py:79` のみ）。欠測処理も
`build_features_wt` 末尾の `df[present] = df[present].fillna(0)`（`:296-297`）と
`prepare_X` の `.fillna(0)` で二重に統一されている。
**差は「読み込む期間」だけ** → 2.5 の `med_rp` 問題に帰着する。

---

## 5. (e) `p3_calibration` の較正窓

- `keirin/src/p3_calibration.py` は**薄いプロキシ**で、正本は
  `backend/src/services/keirin_p3_calibration.py`（importlib で読み込み・`:27-47`）。
- `FIT_WINDOW = "2025-01-01〜2025-12-31"`（正本 `:96`）。係数は 2026-08-20 に再推定。
- 配信は 2026年のレースなので **較正窓の外 ＝ live へのリークは無い**。
- 🟠 ただし係数は「現行モデルで 2025年をバックフィルした予測」に当てて推定されている。
  現行モデルは `full_refit: true` で 2022-12〜現在を学習しているので、
  **2025年の予測は in-sample**。in-sample の較正ずれは真の較正ずれより小さく出るため、
  係数（a=0.834〜0.997）は **live に対して弱すぎる可能性**がある。**未計測**。
- 🔴 `calibrated_p3_sum_top2` は 7C / 9C のゲート閾値（1.44 / 1.30）に直結する
  （`main.py:1354`）。2025年を対象にした検証はこの較正について in-sample になる。

---

## 6. 本番モデルの refit 状態（評価の健全性）

`data/models/*.meta.json`（実測）:

| モデル | full_refit | 学習期間 | test_from | 学習日 |
|---|---|---|---|---|
| `lgbm_wt_eval`（live の p3） | **false** | 2022-12-01〜 | 2026-06-15 | 2026-09-13 |
| `lgbm_wt_win`（live の pw） | **true** | 2022-12-01〜現在 | — | 2026-09-14 |
| `lgbm_wt_top2` | **true** | 同上 | — | 2026-09-14 |
| `lgbm_wt_bad` | **true** | 同上 | — | 2026-09-10 |
| `lgbm_wt`（3着内） | **true** | 同上 | — | 2026-09-14 |

- live（未来のレース）に対しては全て honest。
- **過去に当てる操作（`run_live(mode="paper")`・backfill・ROI再集計）は
  `lgbm_wt_win` 以下4本について完全に in-sample。** `build_type_lab_picks.py` は
  `run_paper_vintage`（`:313-`）と `--models board` を用意してこれを避ける設計だが、
  `run_live` を過去日に直接回せば in-sample になる経路は残っている。
- `full_refit: true` のメタに記録されている `test_auc_holdout`（0.8293 等）は
  **in-sample 値**であり、汎化性能として読んではいけない。

---

## 7. 確認できなかった点 / 限界

1. **朝の wt_entries スナップショットが存在しない**（`\d` で `updated_at` なし・
   スナップショットテーブルも無い）。したがって「朝の値 vs 現在の値」を
   同一行で直接比較することはできなかった。2.2 は「節内で不変」という
   間接的だが強い証拠であって、直接比較ではない。
2. 2.3 の skew の規模は「入稿を止められたレース」でしか数えられていない。
   **生成側で退化入力のまま p3 が作られたレースの総数は未計測**。
3. 2026-06 の `race_point` 破損の**原因は特定できていない**。
4. (c) の「学習=walk-forward p3 / 配信=本番 p3」の分布差の大きさは**未計測**。
5. (e) の「in-sample 較正が弱すぎる」は**推測**であり、測っていない。
6. `wt_odds` / `wt_odds_snapshot` の全走査を避けたため、
   オッズの上書き実態（朝オッズが確定値に潰されているか）は**測っていない**。
