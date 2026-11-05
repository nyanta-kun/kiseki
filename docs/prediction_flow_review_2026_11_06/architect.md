> 統合版: `docs/prediction_flow_redesign_plan_2026_11_06.md`（採否・段階計画はそちらを正とする）

# 予想パイプライン設計見直し — 目標フロー（01〜04）とのギャップと段階改修設計

作成: 2026-11-06 / 設計部 / 対象: 中央（JRA）・地方（NAR）
前提: CLAUDE.md・docs/PROJECT_STATUS.md・既存計画書を読み、本番の呼び出し経路と本番 DB（読み取り専用）で裏を取った。
リポジトリは一切編集していない。

---

## 0. 結論（先に）

1. **図のとおりに「位置取りの多段シミュレータで着順を作り、それで確率を出す」形へ作り替えることは勧めない。**
   予測精度は LightGBM（v28）に負ける見込みが高く（構造モデルで負けた前例 v25 がある）、多段位置取りを当てはめるデータも無い（§3.2）。
2. **ただし「能力分布＋モンテカルロ」という核は、今まさに足りない部品として実利がある。**
   v28 は単勝ヘッドと複勝ヘッドが独立になり、単勝順位と複勝順位の**交差**が 610R に出ている。
   この 2 つの周辺確率と矛盾しない**着順の同時分布**を作れるのは、馬ごとにばらつき σ_i を持つ
   ランダム効用モデル（＝能力分布のシミュレーション）だけである（Harville／Henery は原理的に作れない。
   `docs/jra_winplace_structure_plan_2026_09_04.md` §2）。
   → **「LightGBM の出力を、シミュレータの入力（能力の平均 μ_i・ばらつき σ_i）に変換する」**のが推奨案。
   図の 02（展開・馬場）は、後の段で**馬どうしを相関させる共有ショック**としてここへ載せる。
3. 実利の範囲を先に固定しておく。**回収率は作れない**（控除率の壁。単勝で市場との差 +0.164 nat
   ＝モデルが持つ情報は市場の約 75%、PROJECT_STATUS §3.1・§3.2）。作れるのは
   **(a) 連対率・馬連・ワイド・三連複の確率を矛盾なく出すこと、(b) それを proper scoring rule で前向きに
   検証できる形にすること、(c) 欠けている環境情報（馬場バイアス・ペース）を検証付きで足すこと**である。
4. **調べる過程で、出発点の前提を動かす事実が 3 つ見つかった**（§1）。特に
   **中央の開催馬場バイアス（`meet_bias.py`）が 91.6% のレースで中立値に落ちている**こと、
   **地方の勝率・複勝率がレース内で正規化されていない**こと（Σ勝率 p10–p90 = 0.70〜1.38）。

---

## 1. 出発点の訂正（コードと本番 DB で裏を取った事実）

### 1.1 🔴 中央の開催バイアス `meet_bias.py` が 91.6% のレースで働いていない

- `backend/src/indices/meet_bias.py:61-69` `_extract_kai()` は `jravan_race_id[8:10]` を返すが、
  ID の並びは `年4 + 月日4 + 場2 + 回2 + 日2 + R2`（CLAUDE.md・DB 実測 `2026110105041101`）なので
  **[8:10] は場コード**。docstring の例（`"2026032209011012"` → kai="01"）も自身のコードと矛盾している。
- その結果 `meet_bias.py:141` の LIKE が `{年}____{場}{場}%` になり、**回＝場コードの開催でしか一致しない**。
- 実測（本番 DB）:
  - 2024 年以降の中央 9,973R のうち、回＝場コードは **840R（8.4%）だけ**。残り 9,133R は常に中立値（0.0）
  - 4回東京 2026-11-01 の 5R で `_compute_bias` と同じ条件を手で打つと **0 行**。正しいパターン
    `2026____0504%` なら同開催の先行 **120R** が取れる
- 影響先: `frame_bias.py:220`（内外バイアス補正）と `pace.py:217`（前後バイアス補正）。
  どちらも v26 系 34 列の `position_advantage` / `pace_index` として 4 ヘッドへ入っている。
- `meet_bias.py:149` は `Race.date < before_date` なので、**直っていても当日の先行レースは使わない**。
  図の「当日馬場傾向」は現実装では構造的に出せない。
- 🔴 **直すだけでは駄目**。学習はこの「ほぼ定数」で行われている（`composite.SUBINDEX_SOURCE_SQL` が DB の
  保存値を読む）。配信だけ直すと train/serve skew になる。直すなら
  「純関数化 → 学習側で同じ関数 → サブ指数の全期間再計算 → 再学習 → デプロイ → バックフィル → 当日・翌日 calculate」
  を一組で行う（§5 Phase 4）。
- `check_feature_health.py` は月次の sd を見るため、この型の故障（他の成分があるので sd>0 のまま）は捕まらない。

### 1.2 🔴 地方の複勝確率は Harville ではなく、勝率・複勝率ともレース内で正規化されていない

- `backend/src/indices/chihou_calculator.py:840-841`: `win_probs` = is_win ヘッドの生値、
  `place_probs` = is_top3 ヘッドの生値（0〜1 にクリップするだけ）。Harville は LGB が使えないときの
  フォールバック（同 `:853`）だけ。
- 実測（v14・2026-10 の 1,278R）: Σ勝率 平均 1.040・**p10 0.698 / p90 1.378**、
  Σ複勝率 平均 3.083・**p10 2.514 / p90 3.662**。
  画面（`ChihouRaceDetailClient.tsx`）に出ている勝率・複勝率は、レースによって合計が 70%〜138% になる。
- 中央は `composite.py:972`（Σ勝率=1）と `composite.py:988` `normalize_place_to_slots`（Σ複勝率=place_slots）で正規化済み。
- 組合せ確率を作る前に、地方はここを直す必要がある（§5 Phase 1）。激走の判定は自分で q を Σ=3 に
  正規化している（CLAUDE.md「地方競馬 激走 / 見送り」）ので、読み出し側で正規化すれば影響しない。

### 1.3 組合せ確率の既存資産はサービング経路から切り離されている

- `backend/src/betting/` 配下は `src/api` / `src/services` / `src/indices` から **import 0 件**（grep で確認・
  winplace 計画 訂正2 と一致）。`finish_order.py` を使うのは分析スクリプト（`compare_finish_order_models.py` 等）だけ。
- 🔴 `models/finish_order_lambda.json` の Henery λ（0.43〜0.48）は **v26 の過信した softmax 確率に合わせた値**で、
  較正済みの v27/v28 に当てると NLL が悪化する（winplace 計画 訂正1: 現行分布の最適 λ は 0.88〜0.89 で改善ほぼ無し）。
  **この λ を再利用する案は書かない。**
- 既存の有効だった方向は「2着・3着ヘッド＋Henery 混合で三連複3点 0.868→0.895」（PROJECT_STATUS §3.2）。
  ただしこれは v26 時代の測定なので、v28 の上でやり直す（§5 Phase 0 の比較腕に入れる）。

### 1.4 その他（設計に効く事実）

| 事実 | 根拠 | 設計への影響 |
|---|---|---|
| 中央の `passing_1` 44%・`passing_2` 50%・`passing_3/4` 99%・`margin` 全行 NULL | winplace 計画 訂正5 | 「START→中間→直線」の START 段を当てはめる材料がほぼ無い |
| 各馬の区間は「前半＝走破−上がり3F」と「上がり3F」の **2 区間にしか分解できない**。`last_4f` 全行 NULL | `docs/jra_lap_feature_plan_2026_09_16.md` §1 | 多段の「消耗」を馬ごとに当てはめられない |
| 地方 `passing_1` 64.5%・`passing_4` 88.4%・`lap_times` の 73.8% が全ゼロ | `backend/docs/chihou_column_availability.md` | 地方のペース実測は南関中心の約 26% しか無い |
| 内外の位置・走行距離は netkeiba マスターコース（**有料・未契約**）でしか取れない。公開は翌週金曜 | `docs/netkeiba_running_data_plan_2026_09_17.md` | 位置取りシミュレータは有料依存。承認が要る |
| 中央は発走前に WE（天候馬場状態）を取り込む経路がある | `windows-agent/jvlink_agent.py:1016`・`importers/jvlink_parser.py:555` | 馬場状態の「静的適性」は配信時にも入りうる（実充足は Phase 0 で確認） |
| `pace_handicap` は本番で実質使われていない（保存もされず、v24 線形和にしか入らない） | winplace 計画 訂正3 | 「展開」の既存実装は事実上 `pace_index`（決め打ち）と `pace_handicap_pit`（v28 の1列）だけ |
| 中央の前向き記録 `hit_tier_picks` は**全馬**の win/place 確率・発走前単複オッズ・着順を持つ | `db/models.py:1154-1197` | 組合せ確率は「記録済みの確率から決定的に再計算」でき、スキーマ変更なしで前向き照合できる |
| 前向き記録の件数（本番） | 中央: **v27 216R（08-15〜08-30・複勝は Harville 値）** / **v28 VAL 252R（09-05〜09-27）** / v28 TEST Q4 312R。地方: VAL(〜10-31) 3,142R / TEST 11月 215R | 中央で Phase 0/2 の副窓に使えるのは **`index_version = 28` の VAL 252R だけ**。v27 行は複勝が単勝由来なので R1（§3.3）が B0 と区別できない |
| 中央の exotic オッズは発走前の時系列を **21 日だけ**保持、地方は exotic オッズを**取っていない**（`chihou.odds_history` の `bet_type` は全期間で `win` / `place` の 2 種だけ） | PROJECT_STATUS §2.2・本番 `odds_history` 実測 | 組合せの市場比較は中央だけ。地方は払戻（`chihou.race_payouts`）での的中照合まで |

---

## 2. 段ごとのギャップ表

凡例: ✅ 実装済み・本番で効いている / △ 部分的・暗黙的・不完全 / ✗ 無い

### 2.1 中央（JRA）

| 段 | 判定 | 現状（ファイル・関数） | ギャップ |
|---|---|---|---|
| 主催者公式データ | ✅ | `windows-agent/jvlink_agent.py`（蓄積系・速報系 0B11/0B12/0B14/0B15/0B31）→ `importers/race_importer.py` | — |
| 入力検証・データ整備 | △ | `scripts/check_feature_health.py`（DEAD/SHIFT/SPARSE）、`protocol_dm_orchestrator.report_unrecovered()`、`jra_train_serve_skew_audit.py` | 「別成分があるので sd>0 のまま中身が死ぬ」型（§1.1）を検出できない。サブ指数の**構成要素**単位の監視が無い |
| 01 AS 基礎能力 | ✅（暗黙） | `speed.py`（斤量補正込み）・`last3f.py`・`jvan_dm.py`（gain 72%）→ `composite.py` の順位回帰ヘッド | 明示的な「基礎能力」値は無い（LGB の内部） |
| 01 AAR 静的適性 | △ | `course_aptitude.py`・`distance_change.py`・`going_pedigree.py`・`weight_carried` 列 | 距離・コース・馬場・斤量の適性が別々の手作り指数で、条件適用の構造が無い。`going_pedigree` は当日算出で全馬 50（CLAUDE.md） |
| 01 APS 条件適用後能力 | △（暗黙） | v28 の 4 ヘッド（`composite.py:786` `calculate_and_save`） | 値として取り出せない。説明に使えない |
| 02 Position Profile | △ | `past_form.compute_runner_type`（`passing_4` 由来の4分類）・`pace.py` の脚質 | 確率的な位置分布ではなく4値。前半位置の分散は持たない |
| 02 Track Bias | ✗ | `meet_bias.py`（§1.1 のとおり 91.6% で中立・当日除外） | 実質無い |
| 02 Scenario Model | ✗ | `pace.py:546-564` `_predict_pace`（逃げ馬数で fast/normal/slow を決め打ち）、`past_form.compute_pace_handicap_pit` | 確率ではない。ペースの実測ラベル（`races.first_3f`・`lap_times` 99%）を学習に使っていない |
| 03 入力 LOCK | △ | `services/jra_hit_tier_log.py:270` `snapshot_hit_tier`（発走10分前・`index_version`/`rule_version`） | モデルファイルのハッシュや入力ハッシュは持たない。`calculated_indices` は上書きされる（CLAUDE.md） |
| 03 PEI事前試行／本番シミュ／多段位置取り／能力発揮統合 | ✗ | — | 全て無い |
| 03 着順確定・確率集計 | △ | 単勝 Σ=1（`composite.py:972`）・複勝 Σ=slots（`:988`）・着外率 | **着順の同時分布が無い**。単複の交差 610R を組合せへ写す手段が無い |
| 04 PEI | ✗ | （近い代替: `composite_index`） | — |
| 04 勝率 / 連対率 / 3着内率 | ✅ / ✗ / ✅ | `win_probability` / — / `place_probability`（API `HorseIndexOut`・`api/races.py:372-373`） | 連対率が無い |
| 04 組合せ確率 | ✗ | `betting/finish_order.py`（実験・未配線・λ は旧分布用） | 配信も検証基盤も無い |
| 04 予測保存・照合・Forward | ✅（単複のみ） | `keiba.hit_tier_races/picks`（`jra_pick_snapshot_trigger.sh` 毎分・`settle_hit_tier`） | 組合せ確率と **発走前 exotic オッズ**が残らない（21日で消える） |

### 2.2 地方（NAR）

| 段 | 判定 | 現状 | ギャップ |
|---|---|---|---|
| 主催者公式データ | ✅ | `umaconn_agent.py`（UmaConn）→ `importers/chihou_*` | exotic オッズは未取得 |
| 入力検証・データ整備 | △ | `scripts/check_chihou_feature_health.py` | 確率の Σ 崩れ（§1.2）を誰も見ていない |
| 01 AS | ✅（暗黙） | `speed_index`・`last3f_index`・`last_margin_index` → v14 LGB（`chihou_calculator.py:423` `_LGB_FEATURE_NAMES` 39列） | — |
| 01 AAR | △ | `track_win_rate`・`horse_wet_apt*`・`weight_carried`・`distance` | コース適性・距離適性の専用特徴が無い |
| 01 APS | △（暗黙） | v14 is_top3 / is_win ヘッド | 同上 |
| 02 Position Profile | △（中央より厚い） | `_corner_features_batch`（`chihou_calculator.py:1802`）: `c_early_n`/`c_late_gain_n`/`c_makuri_n`/`front_density` | 4値ではなく連続値。ただし前半位置の分散は無い |
| 02 Track Bias | ✗ | 無い | 地方は 1 場で 1 日 10〜12R あり、当日バイアスの標本が中央より取りやすい |
| 02 Scenario Model | ✗ | `front_density`・`prev_pace_ratio` だけ | ペース実測（`lap_times`）は約 26% しか無い |
| 03 LOCK | △ | `chihou.place_pick_races/place_picks`（毎分・全馬の確率と発走前オッズ） | 同上（モデルハッシュ無し） |
| 03 シミュレーション一式 | ✗ | — | — |
| 03 着順確定・確率集計 | ✗ | 生確率（Σ非正規化・§1.2） | 単勝・複勝すら整合していない |
| 04 勝率 / 連対率 / 3着内率 | △ / ✗ / △ | 非正規化 | — |
| 04 組合せ確率 | ✗ | — | 市場比較の材料（exotic オッズ）も無い |
| 04 予測保存・照合・Forward | ✅（単複のみ） | `chihou.place_picks`・`chihou_pick_settle_trigger.sh` | 組合せ照合は払戻でのみ可能 |

---

## 3. 図をそのまま実装すべきか

### 3.1 「実利」の定義（ここを曖昧にすると過去の失敗を繰り返す）

過去の検証から、この設計で実利と呼べるのは次の 3 つだけ:

1. **proper scoring rule 上の改善**（単勝 多項対数損失・複勝 `place_ll`・連対／組合せの対数損失）。
   判定は walk-forward か前向き記録で、市場との差は**参考として並べる**。
2. **表示の整合**: 画面に出す単勝・複勝・連対・組合せの確率が、同じ一つの同時分布から出ていること。
3. **検証可能性**: 発走前に固定（LOCK）した値を、後から決定的に再計算・照合できること。

回収率の改善は目標にしない（PROJECT_STATUS §3.1「市場と同じ向きの分類器は ROI にならない」・
§3.2「三連複 EV による買い目選別は構造的に不成立」）。

### 3.2 「LightGBM で直接確率」 vs 「能力分布＋位置取りシミュレーションで着順を生成」

| 観点 | 現行: LightGBM 直接（v28） | 図: 構造シミュレーション（多段位置取り） | 推奨: LGB → 能力分布 → シミュレーション |
|---|---|---|---|
| 単勝・複勝の精度 | 既知の最良（2026Q3 で `place_ll` 有意改善） | 手作りの構造は LGB に負けた前例がある（v25 乗数式: 複勝率 −3.42pt、v24 線形和は v26 の足枷） | **周辺分布は v28 をそのまま再現する**（そうなるよう μ・σ を合わせる）ので劣化しない |
| 組合せ・連対 | 出せない | 出せる | 出せる |
| 単複の交差（610R）との整合 | 周辺だけ | 構造次第 | 馬ごとの σ_i で表現できる |
| 馬どうしの相関（前残り・外差し決着） | 出せない | 出せる | 後段（Phase 5）で共有ショックとして足せる |
| 当てはめに要るデータ | 既存 | 各段の位置・区間タイム（中央は2区間・地方は約26%） | 既存（単複の確率だけ） |
| train/serve skew の危険 | 管理済み（`past_form.py` 方式） | 新しい入力が全部リスク | 入力は配信済みの確率だけ。純関数1本 |
| point-in-time | 管理済み | ペース実測・位置実測の PIT 化が必要 | 不要（入力が既に PIT） |
| 検証 | 既存基盤（`jra_prob_scoring.py`） | 多段の各パラメータごとに窓を消費 | 自由パラメータがほぼ無く（Phase 2）、窓をほとんど消費しない |
| 計算量 | 推論のみ | 10万回 × 多段 × 馬 | 10万回 × 馬（1R 0.1〜0.3 秒の見込み・§4.6） |
| 外部依存 | なし | 内外位置には netkeiba 有料契約が要る | なし |

**判断**: 図の 03 のうち「能力分布からの試行 → 着順確定 → 確率集計」は作る価値がある。
「多段位置取り・能力発揮の統合（位置維持・消耗）」は、**当てはめるデータが無く、精度で LGB に勝てる
根拠も無い**ので本体としては作らない（条件付きの Phase 6 に置く）。図の 02 は「LGB の特徴」か
「シミュレーションの共有ショック」として足し、**どちらに載せるかは測ってから決める**。

### 3.3 推奨案の数理（要点だけ）

- 各馬の走りを `X_i = μ_i + σ_i · ε_i`（ε_i は独立）とし、小さい順／大きい順で着順を決める。
- **ε を Gumbel にし σ_i を全馬 1 に固定すると、Plackett-Luce ＝ Harville と一致する**。
  つまり推奨案は現行の暗黙の基準（Harville）を**特別な場合として含む**。σ_i を馬ごとに動かすことが
  唯一の拡張で、効いたかどうかは「全馬 σ=1」との対比較で切り分けられる。
- μ_i・σ_i はレースごとに、**v28 の勝率（Σ=1）と複勝率（Σ=slots）に一致するよう**数値的に合わせる
  （共通乱数での固定点反復）。2n 本の制約に 2n 個の未知数（平行移動・尺度の不定性と Σ 制約で相殺）なので、
  **学習するパラメータは無い**。
- 合わせる複勝の周辺は「上位 k 着以内」で、k はヘッドのラベルに揃える: 中央 v28 は `place_slots`（5〜7頭立ては 2）、地方 v14 は常に 3（Phase 1 参照）。
- 実行可能条件: `win_i ≤ place_i ≤ 1`。破れる馬はクリップし、当てはめの残差を記録する。
  収束しなければ Harville（σ=1）へ落とし、その旨を返す。
- 出力: 試行から 連対率・馬連・ワイド・三連複（・馬単・三連単）・期待着順・**PEI（＝μ_i を指数化したもの）**。
- 乱数の種は `hash(race_id, method_version)` で固定し、**同じ入力から同じ出力**を保証する（LOCK の前提）。
- 図の「PEI事前試行10万回」は、この μ・σ の当てはめ（事前試行）に当たり、「本番10万回」は当てはめ後の
  最終試行に当たる、と対応づけられる。

---

## 4. 推奨アーキテクチャ

### 4.1 モジュール構成（純関数の正本 ＋ 薄いアダプタ。`past_form.py` と同じ型）

```
backend/src/indices/finish_dist.py            [jra]  ★正本・純関数（numpy のみ・DB/FastAPI 非依存）
    fit_random_utility(win, place, slots, *, family="gumbel", n_fit, seed) -> FitResult(mu, sigma, residual, converged)
    simulate_orders(fit, *, n_sims, seed, shocks=None) -> OrderSample
    summarize(sample, bet_types) -> FinishDist(win, top2, top3, exp_rank, combos{bet_type: {combo: p}})
    harville_baseline(win, ...) -> FinishDist     # σ=1 の比較腕（同じ関数で σ を固定）
    METHOD_VERSION = "ru1"

backend/src/indices/chihou_prob_normalize.py  [chihou] 地方の確率正規化（純関数）
    normalize_race_probs(win_raw, top3_raw) -> (win Σ=1, top3 Σ=3)   # ヘッドのラベルが一律3着以内のため

backend/src/indices/track_bias.py             [jra]   Phase 4（PIT・当日先行レースを含む）
backend/src/indices/chihou_track_bias.py      [chihou]
backend/src/indices/pace_scenario.py          [jra]   Phase 5（ペースの確率モデル・ショック係数）

アダプタ
  api/races.py                     [jra]    GET /api/races/{id}/finish-dist
  api/chihou_races_router.py       [chihou] GET /api/chihou/races/{id}/finish-dist
  services/jra_hit_tier_log.py     [jra]    発走10分前の exotic オッズを保存（Phase 3）
  scripts/finish_dist_scoring.py   [jra]    Phase 0 の採点基盤（walk-forward・前向き記録の両方）
```

- **`betting/` には置かない**。`betting/*` は shared で単独 PR が必要になり、しかも既存の実験資産
  （旧 λ）と混ざって誤用を招く。`finish_dist.py` は jra の柱に置き、地方は**読み取り専用で import** する
  （`keirin_marquee.py` の「正本を一つにして他柱が使う」と同じ考え方。地方側は自分の柱のファイルだけ触る）。
- 正本の規律（CLAUDE.md の作法をそのまま適用）:
  - 学習・配信・検証が**同じ関数**を通る。検証スクリプトに別実装を書かない
  - 「全馬 σ=1 なら既存 `_harville_place_probs` と一致」をテストで固定（数値一致・許容誤差つき）
  - 「同じ入力・同じ種なら同じ出力」をテストで固定

### 4.2 柱（pillars）

| 変更 | 柱 | 備考 |
|---|---|---|
| `indices/finish_dist.py`・`track_bias.py`・`pace_scenario.py`・`api/races.py`・`services/jra_*`・`backend/scripts/*` | jra | |
| `indices/chihou_*`・`api/chihou_*`・`services/chihou_*` | chihou | |
| `db/models.py`・`db/chihou_models.py` の新テーブル定義と `backend/alembic/` | **shared**（`db/models.py`）／chihou（`chihou_models.py`）＋ **shared**（alembic） | Alembic は単独 PR・`--rev-id "$(date +%Y%m%d%H%M)_shared"`。並列生成しない |
| `frontend/src/lib/api.ts`（型追加） | shared | 画面コンポーネントは jra / chihou |
| `composite.py` | shared | Phase 0〜3 では**触らない**（v28 の出力を読むだけ）。Phase 4(a)／5 で LGB の特徴を足すと決まった場合は `V28_FEATURE_NAMES` を変えることになるので、そこだけ shared の単独 PR（版上げ・バックフィル・当日/翌日 calculate の3段つき） |

### 4.3 DB / Alembic の要否

| Phase | 要否 | 理由 |
|---|---|---|
| 0〜2 | **不要** | 組合せ確率は `hit_tier_picks` / `place_picks` に記録済みの全馬 win/place から決定的に再計算できる。配信も `calculated_indices` を読んで都度計算 |
| 3 | **要（shared・単独 PR）** | 中央の**発走10分前 exotic オッズ**は 21 日で消えるため保存先が要る。モデルの入力ハッシュ（LOCK）もここで足す |
| 4〜5 | 場合による | 新特徴を LGB に入れるなら calculated_indices の列追加ではなく、`past_form.py` 方式（都度計算）で済ませられるか先に検討する |

Phase 3 のテーブル案（最小）:

```
keiba.prerace_exotic_odds        -- 発走前 exotic オッズの凍結
  race_id, snapshot_at, lead_minutes, bet_type, odds_json(JSONB: {"1-2": 12.3, ...})
  PK(race_id, bet_type)
keiba.hit_tier_races に追加
  model_sha256 TEXT     -- 読み込んだモデルファイルの指紋（LOCK）
  finish_dist_version TEXT
```

組合せ確率そのものは保存しない（決定的に再計算できるので冗長。18頭の三連複は 816 通り×36R/日になる）。

### 4.4 API 形（案）

```
GET /api/races/{race_id}/finish-dist?bet_types=quinella,wide,trio&top=30
{
  "race_id": 123, "method_version": "ru1", "index_version": 28,
  "fit": {"converged": true, "max_abs_residual": 0.0007, "fallback": null},
  "horses": [
    {"horse_number": 5, "win": 0.231, "top2": 0.384, "top3": 0.512,
     "exp_rank": 3.9, "pei": 61.2}
  ],
  "combos": {
    "quinella": [{"combo": [5, 8], "p": 0.071}, ...],   // 上位 top 件・確率降順
    "wide":     [...],
    "trio":     [...]
  },
  "note": "確率は的中しやすさの目安で、払戻の期待値ではありません"
}
```

- `win` / `top3` は v28 の値と（当てはめ残差の範囲で）一致する。一致しない場合は `fit` に出す。
- オッズを使わないので、レースごとに 1 回計算してプロセス内キャッシュ（既存の推奨 API と同じ 60 秒）で足りる。
  `calculated_indices` が再算出されたら（馬体重・パドック到着）キャッシュを無効化する。キーは入力確率そのもののハッシュにする（`calculated_at` は UTC と JST が混在する列なので、前後関係の判断に使わない・CLAUDE.md）。
- 🔴 回収率・EV を名乗るフィールドは置かない（地方の EV ランクを撤去した経緯と同じ）。

### 4.5 LOCK（図の「入力評価の固定」）

既存の前向き記録がほぼ LOCK の役を果たしている。足りないのは
**(1) 読み込んだモデルファイルの指紋、(2) finish_dist の版、(3) 中央の発走前 exotic オッズ**の 3 点だけ。
シミュレーションの出力は (入力確率, 版, 種) から決定的に再現できるので、出力自体を凍結する必要は無い。

### 4.6 計算量の見積り（実装前の見込み・要実測）

- 18頭 × 10万回の Gumbel 乱数生成＋上位3頭の部分ソート＋組合せの整数符号化と集計: numpy で 0.1 秒前後の見込み。
- μ・σ の当てはめ（2万回 × 20 反復程度）を足して 1R あたり 0.2〜0.5 秒。中央 36R/日、地方 100〜150R/日。
  VPS の backend コンテナで都度計算＋キャッシュで足りる見込み。**Phase 2 で実測してから配信方式を決める**。
- 精度: 三連複の確率 0.002 の組で、10万回の標準誤差は約 7%。採点（対数損失）では低確率の組の誤差が効くので、
  Phase 0 では 100 万回との差を感度分析として必ず出す。図の「10万回」は目安であって根拠ではない。

---

## 5. 段階計画

窓の呼び方（CLAUDE.md・`jra_protocol.py:67`・`chihou_protocol.py`）:
中央 TRAIN ≤2025-06-30 / **VAL 2025-07-01〜2026-09-30** / TEST 2026-10-01〜（四半期ローリング）。
地方 TRAIN ≤2025-06-30 / **VAL 2025-07-01〜2026-10-31** / TEST 2026-11-01〜（月次ローリング）。

### Phase 0 — 組合せ・連対の採点基盤（学習なし・DB 変更なし）

| 項目 | 内容 |
|---|---|
| 目的 | 「どの同時分布が実際の着順を一番よく当てるか」を測る物差しを先に作る。以後の全 Phase がこれで判定される |
| 変更ファイル | 新規 `backend/scripts/finish_dist_scoring.py`（jra）。入力の walk-forward 予測は **`jra_place_head_ab.run_walk_forward`（`backend/scripts/jra_place_head_ab.py:171`）を import** して使う（`p_win` と**独立 is_top3 ヘッドの** `p_top3_raw` を同じ分割で出す）。🔴 `jra_prob_scoring.py` は複勝を `_harville_place_probs` で作るので入力元にしてはいけない（複勝が単勝由来だと §3.3 の σ 当てはめが全馬 σ=1 に収束し、R1 と B0 が区別できず試験が空振りする）。採点式（多項対数損失・`place_ll`）は `jra_prob_scoring.py` と揃える |
| 柱 | jra |
| 測るもの | 単勝 多項対数損失 / `place_ll` / **連対（top2）対数損失** / **馬連・ワイド・三連複の実現組の対数損失** / 交差件数 / 当てはめ残差 |
| 腕 | B0 Harville(v28 単勝) ・ B1 2着・3着ヘッド＋Henery 混合（08-23 の三連複 +0.066 の再現・λ は VAL 前半で当てはめ） ・ M 市場含意（中央のみ・exotic の確定オッズ＝backfill 済み 9,171R。発走前ではないので参考） |
| 窓 | 中央 VAL の walk-forward（2025Q3〜2026Q3）＋前向き記録 **v28 の VAL 252R**（副・`index_version = 28` で絞る）。地方は VAL の前向き記録 3,142R |
| 合格基準 | 基盤の妥当性だけを見る: (a) B0 の単勝・複勝の値が `jra_prob_scoring` の既知値と一致、(b) 実現組の対数損失がレース単位クラスタ bootstrap で CI を出せる、(c) 10万回と100万回の差が判定に効かないこと |
| 依存 | なし |
| 既存計画との接続 | 未着手の `docs/jra_exotic_ev_preregistration_2026_08_23.md` の改訂（PROJECT_STATUS §4.2）と同じ母集団を使う。新しい事前登録を別に立てず、そちらを改訂して接続する |

### Phase 1 — 地方の確率を正規化する

| 項目 | 内容 |
|---|---|
| 目的 | 地方の勝率 Σ=1・3着以内率 Σ=3 を保証する（§1.2） |
| 変更ファイル | 新規 `indices/chihou_prob_normalize.py`、`api/chihou_races_router.py`（返す値を正規化）、テスト |
| 柱 | chihou |
| 方式 | **保存値は変えず、読み出し側で正規化**する（版上げ・全期間バックフィル・3段デプロイを避ける）。🔴 地方の is_top3 ヘッドのラベルは**頭数に関係なく `finish_position <= 3`**（`backend/scripts/train_chihou_market_lgb.py:550`・学習は `head_count >= 6` のみ `:182`）。したがって複勝確率は **「3着以内率」として Σ=3 に正規化**する。5〜7 頭立ての「複勝圏（2着以内）」へ Σ=2 で押し込むと意味が壊れる。複勝圏が 2 着までのレースでは、画面の文言を「3着以内率」とするか、2着以内率を Phase 2 の同時分布から出すかを決める（件数を先に数える）。複勝枠ラベルでの再学習は地方側の将来課題として残す。勝率は Σ=1。頭数は実際のフィールドから数える（`races.head_count` は発走前 NULL）。消費者（`chihou_gekisou_source.py`・`chihou_cutoff.py`・`chihou_recommender.py`・`chihou_place_pick_log.py`）を Phase 冒頭で全部辿り、**生値を前提にしているものは触らない** |
| 検証 | 地方 VAL 前向き記録 3,142R で、正規化前後の 単勝 多項対数損失・`place_ll` をレース単位 bootstrap で比較 |
| 合格基準 | どちらの指標も悪化しない（95%CI 上限 ≤ 0）。悪化するなら、正規化ではなくヘッドの較正の問題なので Phase を止めて報告 |
| 依存 | Phase 0 の採点関数 |

### Phase 2 — 能力分布シミュレータ（`finish_dist.py`）と中央の評価

| 項目 | 内容 |
|---|---|
| 目的 | v28 の単勝・複勝に一致する同時分布を作り、連対・組合せの確率を出す |
| 変更ファイル | 新規 `indices/finish_dist.py`・`tests/test_finish_dist.py`、`finish_dist_scoring.py` に腕 R1 を追加 |
| 柱 | jra |
| 腕 | R1 = Gumbel・馬ごと σ_i（当てはめ）。比較は B0（R1 で σ=1 固定と同値）・B1 |
| 窓 | 中央 VAL walk-forward（主・独立複勝ヘッドの予測）＋前向き記録 **v28 の VAL 252R**（副）。**TEST は使わない** |
| 主指標 | 三連複の実現組 対数損失 Δ(R1 − B0)。副: 馬連・ワイド・連対・交差件数・当てはめ残差 |
| 合格基準（事前固定） | 主指標の 95%CI 上限 < 0 なら「採用」。CI が 0 を跨いでも**非劣性**（上限 < +0.005 nat・事前に固定）なら「整合のために採用」。それより悪ければ B0（σ=1）で配信し、σ 拡張は捨てる |
| 依存 | Phase 0 |
| 地方 | 同じ正本を Phase 1 後の確率に当て、地方 VAL 前向き記録で同じ判定（市場比較なし） |

### Phase 3 — 配信と前向き記録（連対率・馬連・ワイド・三連複）

| 項目 | 内容 |
|---|---|
| 目的 | 画面に出し、発走前に固定したものを後から照合できるようにする |
| 変更ファイル | `api/races.py`・`api/chihou_races_router.py`（`/finish-dist`）、`frontend/src/lib/api.ts`（shared）、レース詳細コンポーネント（jra / chihou）、`services/jra_hit_tier_log.py`（exotic オッズの凍結）、Alembic（§4.3・shared 単独 PR を**先に**入れる） |
| 柱 | shared（Alembic・api.ts）→ jra → chihou の順に別 PR |
| 検証 | 配信開始後の前向き記録で Phase 2 と同じ指標を月次で集計。中央は**発走10分前 exotic オッズ**の市場含意を並べる |
| 合格基準 | 運用監視として: 当てはめ失敗率 < 1%、フォールバック件数、前向きの対数損失が VAL の水準から外れないこと（外れたら原因調査） |
| 依存 | Phase 2 の採用判定 |
| 表示の注意 | 「的中しやすさ」の表示であり、期待値・推奨ではない。tier の復活ではない |

### Phase 4 — 馬場バイアス（Track Bias）を PIT で作り直す

| 項目 | 内容 |
|---|---|
| 目的 | 中央の死んだ開催バイアスの代わりと、地方の当日バイアスを、検証付きで足す |
| 前提調査 | (1) 当日の先行レースの結果が、後のレースの発走10分前までに DB に入っている割合（中央 0B12・地方 0B12＋5分おきの fetch-results）を実測する。`race_results` に取込時刻の列が無ければ、Phase 3 のスナップショット時に「その時点で結果が入っている同場先行レース数」を記録して測る。(2) PIT の定義: 学習時は「発走時刻＋到着遅延 L 分 < 対象レースの発走10分前」の先行レースだけを使う |
| 変更ファイル | 新規 `indices/track_bias.py`・`indices/chihou_track_bias.py`（純関数＋一括/レース単位アダプタ）、A/B スクリプト |
| 柱 | jra / chihou（別ブランチ） |
| 載せ方の比較 | (a) LGB の特徴として（ラップ7特徴と同じ walk-forward A/B。採用時は `composite.py` を触るので shared 単独 PR） (b) シミュレータの共有ショックとして（内枠・前行き馬を同時に動かす。Phase 5 と同じ枠組み） |
| 既存の `meet_bias.py` | **その場で直さない**。現行モデルはほぼ定数で学習済みなので、直すと skew になる。新特徴が採用されたときに、再学習とあわせて `frame_bias`/`pace` 側の扱いを決める（そのとき初めて「サブ指数の全期間再計算 → 再学習 → 3段」） |
| 窓・指標 | 中央 VAL walk-forward（四半期等重み）で単勝 多項対数損失と `place_ll`。地方 VAL（月次）で同じ |
| 合格基準（事前固定） | 単勝 多項対数損失 Δ の 95%CI 上限 < 0 かつ全窓の過半で改善。満たさなければ不採用で、定義をいじって VAL を回し直さない |
| 依存 | Phase 0（物差し）。(b) は Phase 2 |

### Phase 5 — 展開シナリオを確率にし、馬どうしの相関をシミュレーションに入れる

| 項目 | 内容 |
|---|---|
| 目的 | 図の Scenario Model。ペースが速い/遅いときに**同じ脚質の馬がまとめて浮き沈みする**相関を組合せ確率へ入れる。周辺分布（単複）は変えない |
| 方式 | (1) ペースの実測ラベル: `races.first_3f`（中央 99%）の、場・距離・馬場・クラス別の PIT 中央値との差で fast/normal/slow。(2) P(ペース \| 出走馬の脚質構成・コース特性・頭数) を小さな多項ロジットで学習（TRAIN）。(3) シミュレーションで `X_i = μ_i + β_style(i) · S + σ_i ε_i`、S はペースの試行。β は VAL 前半で当てはめ、μ・σ は**ショック込みで単複に再一致**させる |
| 変更ファイル | 新規 `indices/pace_scenario.py`、`finish_dist.py` の `shocks` 引数、スクリプト |
| 柱 | jra（地方はペース実測が約26%なので、南関に絞るか見送り） |
| 検証 | (1) ペースモデル: VAL 多項対数損失を「気候値（場・距離の頻度）」「現行 `pace.py:546` の決め打ち」と比較。(2) 相関: 三連複・馬連の実現組 対数損失 Δ(R2 − R1)。VAL を前半（β 当てはめ）と後半（確認）に分ける |
| 合格基準 | (1) が気候値に勝たなければ (2) に進まない。(2) は Δ の 95%CI 上限 < 0 |
| 依存 | Phase 2。Phase 4 の (b) と同じ枠組みを共有 |

### Phase 6（条件付き・既定では着手しない）— 多段位置取りシミュレータ

| 項目 | 内容 |
|---|---|
| 着手条件 | Phase 5 で相関の上乗せが出て、なおかつ「位置」の情報を増やせば伸びるという診断がある場合のみ |
| 前提 | netkeiba マスターコース契約（**有料・月額は契約前に要確認・ユーザー承認必須**）。収録は個別ラップ 2024〜・走行距離／内外 2025〜、公開は翌週金曜（`docs/netkeiba_running_data_plan_2026_09_17.md`）。取得は約 31 時間 |
| 判定 | 単勝・複勝で v28 に劣後せず、組合せで Phase 5 に勝つこと。どちらかを満たさなければ作らない |

### 補助（任意・低コスト）— 01 の AS / AAR / APS を「説明」として出す

v28 単勝ヘッドの寄与（LightGBM `pred_contrib`）を「能力系（speed・last3f・DM 等）」「適性系（course・distance・
going・weight_carried 等）」「環境系（frame・pace・meet 等）」に束ねて、馬ごとに表示する。
**精度は変えない説明表示**であり、検証は「寄与の合計＝予測値」の恒等式テストだけ。柱 jra。
地方 v14 も同じ方法で可能。図の 01 を「作り直す」より、既存の能力評価を**見える化**する方が安く確実。

---

## 6. 窓の予算（多重比較を避ける）

- 中央の TEST 2026Q4 には既に **3 件の確認待ち**がある（ラップ7特徴→v29・調教 ck_x・複勝 単複比ルール A）
  （PROJECT_STATUS §4.2・`JRA_TEST_USAGE_LEDGER.md`・memory）。ここへ本計画の採否を重ねない。
- **Phase 0〜2 は VAL（walk-forward）と VAL 内の前向き記録だけで決める。** Phase 2 の R1 は学習パラメータを
  持たないので、VAL の消費は軽い。
- TEST を使う確認は **2027Q1 以降に 1 本ずつ**、走らせる前に事前登録し、`record_test_usage()` で台帳へ。
  地方は月次なので、2026-12 か 2027-01 の 1 か月を Phase 1・2 の確認に充てる（激走の再確認と同じ月に重ねない）。
- Phase 4・5 は当てはめるパラメータがあるので、VAL を「当てはめ」と「確認」に分けてから TEST に進む。
- ⚠️ 中央は四半期ごとに開催地が総入れ替えになるので、四半期をまたいで数字を比べない（CLAUDE.md）。

---

## 7. リスクと対策

| リスク | 内容 | 対策 |
|---|---|---|
| 周辺確率の崩れ | シミュレータの単複が v28 と食い違う（当てはめ不良・実行可能条件違反） | 当てはめ残差を API とログに出す。閾値超えは σ=1 へ落とす。残差の分布を Phase 2 の副指標にする |
| 地方の正規化の波及 | 生値を前提にした消費者がいる | 保存値は変えず読み出し側で正規化。消費者を Phase 1 冒頭で全部辿る |
| モンテカルロ誤差 | 低確率の組で対数損失が揺れる | 100 万回との感度分析。種固定で再現性を担保 |
| train/serve skew | Phase 4・5 の新特徴 | `past_form.py` 方式（純関数＋2アダプタ・パリティテスト）を必須にする |
| PIT の破れ | 当日バイアスで「結果がまだ入っていない先行レース」を学習で使う | 到着遅延 L を実測し、学習時も同じ L で切る |
| meet_bias の扱いを誤る | 「バグだから直す」で配信だけ直す | §1.1 のとおり、再学習とセットでしか直さない。それまでは「死んでいる」と明記して残す |
| 表示の誤読 | 組合せ確率を「買い目」「期待値」と受け取られる | API・画面とも「的中しやすさ」と明記。EV・回収率の欄を作らない |
| 計算負荷 | 地方の多レース日に VPS が詰まる | 都度計算＋キャッシュ。Phase 2 で実測して、足りなければ calculate 時に 1 回計算して保存する方式へ切り替え |
| 前向き記録の欠落 | 中央の発走前 exotic オッズが 21 日で消える | Phase 3 の保存テーブル。それまでは 21 日以内に回す集計ジョブで凌ぐ |
| shared の並列 | Alembic・api.ts を他ブランチと並行して触る | Phase 3 の shared 部分を単独 PR で最初に入れ、他は rebase |

---

## 8. 再検証しない（否定済み・決着済み）

- tier（購入指針）・指数信頼度の復活（2026-10-24 廃止。オッズの言い直し）
- 旧 Henery λ（0.43〜0.48）の再利用・λ を表示経路へ繋ぐ（較正済み確率では効果なし）
- 三連複 EV による買い目選別（構造的に不成立）・WIN5
- 市場特徴を「配信時にオッズを渡さないまま」学習に入れる（地方 v13→v14 で 9pt 落とした）
- composite の min-max 15〜85 化（中央・地方とも禁止）
- DB に保存した過去の指数・確率での ROI／的中率評価（in-sample）
- 中央: レース内相対化・レース単位の本命崩れ・調教の他馬比較・キャリア0-2走残差ヘッド・人気薄残差モデル・
  paddock／going_pedigree／rebound の除去（いずれも効果なし）
- 地方: reg_rank 目的関数・着外率ヘッド・JRA 戦歴・未使用の高充足列・sweet_spot（model-vintage look-ahead）・
  中央と同じ walk-forward の横展開
- 乗数式の構造モデル（v25: 複勝率 −3.42pt）を、名前を変えて作り直すこと

---

## 9. 外部依存・費用

| 項目 | 費用 | 承認 |
|---|---|---|
| Phase 0〜5 | 追加費用なし（既存の VPS・DB・データ）。VPS の CPU 使用が増える（Phase 2 で実測） | 不要（通常の PR フロー） |
| Phase 3 の Alembic | なし | shared の単独 PR として統合 PM の順序に従う |
| Phase 6 の netkeiba マスターコース | **有料（月額・金額は契約前に要確認）** | **ユーザー承認必須**。お試し 14 日で取得を回せる設計は既にある |
| 地方の exotic オッズ取得（任意） | 費用なし。ただし UmaConn COM の占有が増える | 実施するならユーザー判断（realtime への負荷と、過去の COM 競合事故を踏まえて） |

---

## 10. 比較して捨てた案

| 案 | 捨てた理由 |
|---|---|
| A. 図のとおり多段位置取りシミュレータを予測の本体にする | 当てはめるデータが無い（中央は区間2つ・`passing_1/2` 半分以下、地方は lap 約26%）。構造モデルが LGB に負けた前例（v25・v24）。採否に窓を多く使う |
| B. 現状維持（組合せは出さない） | 単複の交差 610R がある以上、画面で単複・連対・組合せを出すなら矛盾しない同時分布が要る。地方は単複すら Σ が崩れている |
| C. Harville（v28 単勝）だけで組合せを出す | 安いが、複勝ヘッドの情報（2026Q3 で有意に効いた分）を組合せへ写せない。Phase 2 の比較腕 B0 として残し、R1 が非劣性に届かなければこれで出す |
| D. 組合せごとに専用の LGB ヘッドを作る（連対ヘッド・2着ヘッド・3着ヘッド） | ヘッドが増えるたびに相互の整合が崩れる（Σ 制約・単調性）。連対率は同時分布から出せるので、独立ヘッドは同時分布の連対が較正不良だった場合の予備に回す |
| E. 市場オッズとモデルの確率を混ぜて配信する | 精度は上がる（0.831→0.866 の前例）が、人気薄の識別という用途を壊し、配信時にオッズ必須になる。表示用途では採らない |
| F. 新しい正本を `betting/` に置く | shared で単独 PR が要り、旧 λ 等の実験資産と混ざる。jra の `indices/finish_dist.py` に置き地方は import する |
