# 計画書: レース波乱度分解による穴馬期待値スコア化

- 起草日: 2026-09-02
- 対象柱: `jra` / `chihou`（`keirin` は対象外）
- 状態: **草案・未着手**。事前登録も TEST 消費もまだ行っていない
- 前提文書: `docs/chihou_rebuild_2026_08.md` 17章 / `HANDOFF_2026-08-23.md` / `backend/docs/chihou_exotic_type_lab_2026_08_29.md` §10 / `docs/jra_new_index_results.md` Phase3

---

## 0. 一行で言うと

**「誰が来るか」を当てるのは限界に達したので、「席が空くか」を当てる側に情報源を移す。**

```
穴馬スコア = P(このレースで人気薄に席が空く)        ← レース側モデル（オッズ以外の変数）
           × 人気薄内で正規化した市場複勝確率        ← 馬側（単勝オッズ → Stern/Henery 変換）
           × 指数補助フィルタ                        ← 既存リランカー等で足切りするだけ
```

---

## 1. 目的とスコープ

### 1.1 なぜこの形なのか（確定済みの事実に基づく）

| # | 事実 | 出典 |
|---|---|---|
| 1 | 人気薄の中の precision は尽きている。地方2年 walk-forward で指数1位 20.8% / 3位 21.9% と差が無い | `docs/chihou_rebuild_2026_08.md:1201-1222` |
| 2 | 詰まっているのは recall。来た人気薄を指数5位内に置けた率 **27.8%**、レース単位捕捉率 **31.9%**（⚠️ **ゲート無し**＝全レースで指数5位内を見た場合の値。現行商品はゲートで71%のレースを捨てており実効 recall は約 10.5%・§11.2） | 同上 |
| 3 | JRA では指数の複勝上乗せが walk-forward 実測で**有意にマイナス** −0.022 [−0.040, −0.003] | `HANDOFF_2026-08-23.md:34` |
| 4 | 荒れ率は頭数でほぼ決まる（8頭 35.2% → 12頭 57.1% → 14頭 64.0%・25,744R） | `docs/chihou_rebuild_2026_08.md:1186, 1325` |
| 5 | 単純ルール `odds_top1 ≥ 3.5` は選択率 16.7% で hit 34.95%（base 19.53%）＝ **lift 1.79** | `backend/models/chaos_classifier_v1_metrics.json` の `simple_rules.odds_top1_ge35` |
| 6 | 学習済 chaos_classifier は AUC 0.711、test の top10% lift 2.342 が **fresh では 1.667** まで落ちる | 同上 `results.a.lift_test` / `lift_fresh` |
| 7 | JRA `entropy_norm` は本番配線済み。tier C を複勝的中率 11〜13pt 分離 | `backend/src/indices/confidence.py:38-47` |
| 8 | 確定オッズで組んだ実験は2回崩壊した（§7.1） | `docs/chihou_rebuild_2026_08.md:628` / `backend/docs/chihou_exotic_type_lab_2026_08_29.md:20, §10` |

事実5と6の並びが本プロジェクトの設計を決めている。**学習した分類器は、選択率を揃えると2変数の手置きルールに勝てていない**（fresh の top10% lift 1.667 < 手置きルールの選択率16.7%での lift 1.79）。だから本計画は「まずベースラインを測り、それに勝てなければ分類器を作らない」順序を強制する。

### 1.2 商品としての位置づけ

🔴 **これは的中率商品であり、ROI 商品ではない。ROI の改善を約束しない。**

理由:
- JRA の複勝市場には favorite-longshot bias が**本命側有利**の向きで存在し、EV が高い馬ほど ROI が下がる実測がある。
- 既存の穴系商品はいずれも ROI < 1.0 で頭打ち（地方 注目馬 0.868 [0.557, 1.233] / 断然人気穴 0.773 [0.619, 0.951]・`backend/src/services/chihou_recommender.py:855-859`）。地方リランカーも複勝 ROI ~0.83（`backend/src/indices/chihou_upset.py:12`）。
- 人気割れレースでは穴のオッズが圧縮されるため、「席が空きやすいレース」を当てても払戻は上がらない構造がある（§7.3）。

したがって**採否判定は recall 系 KPI のみで行う**。ROI は記録・報告するが、判定式には入れない。

### 1.3 スコープ外（明示）

以下は否定済みまたは方針外。本計画では扱わないし、蒸し返さない。

| 項目 | 否定の根拠 |
|---|---|
| 穴馬専用モデル / 穴馬重み付け学習 | `docs/chihou_rebuild_2026_08.md` 17.3・17.8 表 |
| 血統追加（産駒成績の集計） | 同 17.7（walk-forward 2年で増分ゼロ） |
| オッズ時系列（資金の流れ） | 同 17.4（水準との交絡） |
| EV ゲート単体 | `docs/jra_exotic_ev_preregistration_2026_08_23.md` の主基準未達 |
| レース単位「本命崩れ」特徴を馬側モデルへ足す | `backend/scripts/JRA_TEST_USAGE_LEDGER.md` 2026-08-16 `jra_race_level_walkforward.py` **不採用** |
| 三連単高配当を狙う商品 | `backend/docs/chihou_exotic_type_lab_2026_08_29.md` §10「やらない」 |

⚠️ 最後の2つとの違いを明示しておくこと。`jra_race_level_walkforward.py` が否定したのは「レース単位の値を**馬側モデルの特徴として配る**」こと（レース内で定数の列は順位を動かせない）。本計画はレース単位の値を**レース単位のラベルに対して**使い、馬側とは掛け算で合成する。同じ材料だが別の使い方であり、前者の否定は後者を否定していない。**この区別は事前登録に明記する。**

---

## 2. 用語とラベル定義

### 2.1 複勝枠数 `place_slots`

```
place_slots = 3 if head_count >= 8 else (2 if head_count >= 5 else 0)
```
既存実装 `backend/scripts/anagusa_top3_walkforward.py:152` と同一。**7頭以下は複勝が2着までしか払われない**ので、S を常に3として扱うと 5-7頭立てでラベルが壊れる。

### 2.2 席数 S と空席数 E（レース単位ラベル）

- `S` = 3着内（正確には `finish_position <= place_slots`）に入った**上位人気馬**の数
- `E = place_slots − S` = 人気薄に空いた席の数

「上位人気」は発走前オッズ順位 `pop_rank <= P0` で定義する。`P0` は §2.3 で決める（初期案 `P0 = 5`）。

**全レースに 0〜place_slots のラベルが付く**のが本設計の要点。「人気薄が3着内に来た」という低頻度イベントを直接学習させると正例が薄いが、E は全レースに定義できる。

学習は
- 主: `E` の順序回帰 / `P(E >= 1)` の二値
- 従: `P(E >= 2)`（複数の穴が絡む＝より荒れた決着）

の2本を並べ、Phase 1 で片方に絞る。

### 2.3 「人気薄」の定義 — 両論と決め方

| 案 | 定義 | 長所 | 短所 |
|---|---|---|---|
| **A: 人気基準** | 発走前オッズ順位 `pop_rank >= 6` | 頭数によらず「上位5頭 vs それ以外」で意味が一定。既存 `chihou_is_place_pick` と同じ土俵（`backend/src/indices/buy_signal.py:505` 付近の下限定数） | 8頭立てでは人気薄が3頭、16頭立てでは11頭。**母集団サイズが頭数に強く依存**する |
| **B: オッズ基準** | 単勝オッズ ≥ 10倍（`chihou_race_upset_index.py:32` `UNPOP_ODDS_MIN=10.0`）。JRA 既存軸は `[10, 15)`（`buy_signal.py:261-262`）、事前登録の穴帯は ≥12倍 | 払戻の意味が一定。既存の JRA/地方リランカー（`upset_reranker.py` / `chihou_upset.py`）とそのまま接続できる | 混戦レースでは10倍超が10頭出る一方、堅いレースでは0頭。**レースによって母集団密度が激変**する |

**決め方（Phase 0 で機械的に決定し、以後動かさない）**:
1. 両定義で `E >= 1` の base rate を頭数ビン（5-7 / 8-9 / 10-11 / 12-13 / 14-16 / 17-18）別に集計する。
2. **頭数を条件付けたときに base rate の分散が小さい方**を主定義に採る。頭数で base rate が動きすぎる定義は、レース側モデルが「頭数の言い換え」に退化しやすい。
3. 主定義1本・従定義1本を事前登録に書き、以後は主定義だけで採否を決める。従は頑健性チェックの報告用。

⚠️ 人気順位は**必ず発走前オッズ**から作ること。`chihou_popularity_ranks()`（`backend/src/indices/buy_signal.py:557`）が正本。確定人気で作ると §7.1 の事故を繰り返す。

### 2.4 馬側因子: 人気薄内で正規化した市場複勝確率

指数は使わない（§1.1 の事実1・3）。単勝オッズから複勝確率を作る:

1. 単勝オッズ → implied prob → レース内 Σ=1 に正規化（控除率補正）。実装は `calculate_market_chaos()` と同じ手順（`backend/src/indices/confidence.py:63-73`）。
2. Stern/Henery 割引指数変換 `p_i → p_i^λ` で再正規化 → Harville で P(3着内) を導出。実装済み: `backend/src/betting/finish_order.py:66 _henery_adjusted` / `:277 _place_prob_single(method="henery")`、λ は `get_lambda_params()`（既定 0.82）。
3. JRA は Plackett-Luce + Henery 混合が検証済み（`backend/scripts/jra_place_probability_plackett_luce.py`）。**λ は学習期間だけで推定し、評価期間で推定し直さない。**
4. 人気薄集合内で `Σ = 1` に再正規化する。「空いた席を人気薄の誰が取るか」の条件付き確率にするため。

### 2.5 指数補助フィルタ

**スコアの構成要素ではなく足切りに留める。** 候補（いずれか1本を事前登録で固定）:

- 地方: `chihou_is_place_pick` と同じ「指数5位内」（`backend/src/indices/buy_signal.py:617`）。実測リフト ×1.88（`backend/src/services/chihou_recommender.py:855`）
- 地方: 人気薄リランカー `backend/src/indices/chihou_upset.py`（オッズ非使用 logistic・test 精度 37.5%・発走前 −10分でも 30.7% vs 市場同数 23.3%）
- JRA: `jra_upset_axis_tier`（`buy_signal.py:264`・帯 [10,15) × `upset_reranker` 閾値 × バッジ≥1）

「フィルタなし」も必ず腕として並べる。フィルタが recall を削るだけなら外す。

---

## 3. KPI 定義

### 3.1 第一 KPI（採否はこれで決める）— recall 系

| 記号 | 定義 | 現状（地方・指数5位内） |
|---|---|---|
| **R1** | 馬単位捕捉率 = 複勝圏に来た人気薄のうち、スコア上位 k 頭に入っていた割合 | **27.8%** |
| **R2** | レース単位捕捉率 = 人気薄が複勝圏に来たレースのうち、1頭以上を捕捉できた割合 | ゲート無し **31.9%** / 現行商品（ゲート込み）**約 10.5%**（§11.2） |

出典 `docs/chihou_rebuild_2026_08.md:1214-1219`。

🔴 **必ず選択率を揃えて比較する。** k を増やせば R1 は上がるので、「1レースあたり平均何頭選ぶか」「何%のレースに札を出すか」を対照と一致させないと数字に意味がない。報告は必ず `(選択率, R1, R2)` の3つ組で行う。

### 3.2 第二 KPI — 人気を揃えた層化リフト

`strat_diff()`（`backend/scripts/anagusa_top3_walkforward.py:281`、`jra_darkhorse_walkforward.py:260-292` の使用例）で `strata = win_popularity` そのものを揃えた差を測る。市場のエコー分を差し引いた正味の識別力。

**合格水準（既存の凍結値・`backend/scripts/jra_chokyo_walkforward.py:36`）**:
- 5-8番人気: **+1.95pt**
- 9-12番人気: **+0.81pt**

CI は必ずレース単位ブートストラップ（`boot_mean()`・同ファイル `:267`）で出す。

### 3.3 参考指標（判定に使わない）

- precision@k（人気薄帯の複勝圏率）— 頭打ちが確認済みなので改善は期待しない。**悪化していないことの確認**にのみ使う
- レース選別 lift = 選ばれたレースの `E>=1` 率 ÷ 母集団の `E>=1` 率
- 複勝 ROI と CI — **記録するが判定に使わない**（§1.2）
- 較正（Brier / ECE）— レース側モデルの `P(E>=1)` について

---

## 4. フェーズ分割

各フェーズは **DoD（完了条件）を満たしたら次へ、stop rule に触れたら打ち切り**。stop rule に触れた時点で、閾値を動かして探し直さない。

---

### Phase 0 — ラベル分布の実態集計と学習不要ベースライン

**目的**: 学習を始める前に、E の分布と「学習しなくても取れる分」を確定させる。

**成果物（新規スクリプト・DB 書き込みなし）**:
- `backend/scripts/chihou_upset_seat_label.py` — 柱 `chihou`（ファイル名に `chihou` を含めることで `pillars.sh` の `pillar_of` が chihou に分類する）
- `backend/scripts/jra_upset_seat_label.py` — 柱 `jra`

**やること**:

1. **ラベル分布**: 頭数ビン × クラス × 場 × 人気薄定義A/B で `E` の分布（0/1/2/3）を出す。§2.3 の決定手順をここで実行する。
2. **学習不要ベースライン (a) 期待空席数**
   ```
   E_hat = place_slots − Σ_{pop_rank <= P0} p_place_market(i)
   ```
   `p_place_market` は §2.4 の Henery 変換値。オッズ以外を一切使わない。
3. **学習不要ベースライン (b) 2変数ルール**
   `head_count` × `odds_top1` の 2 次元グリッド。既存の `odds_top1 >= 3.5`（lift 1.79・選択率 16.7%）を必ず含める。
4. 両ベースラインについて **選択率を 5% 刻みで振り、(選択率, R1, R2, lift)** の曲線を出す。これが以後すべての比較対象になる。

**発走前オッズ抽出の SQL 骨格**（地方・`backend/scripts/chihou_darkhorse_prerace.py:53-85` を流用）:
```sql
WITH r AS (
  SELECT id, date, head_count,
         to_timestamp(date || post_time, 'YYYYMMDDHH24MI') - interval '9 hours' AS post_utc
  FROM chihou.races
  WHERE date BETWEEN %(start)s AND %(end)s
),
snap AS (
  SELECT DISTINCT ON (o.race_id, o.combination)
         o.race_id, o.combination::int AS hn, o.odds AS pre_odds
  FROM r
  JOIN chihou.odds_history o
    ON o.race_id = r.id
   AND o.bet_type = 'win'
   AND o.fetched_at <= r.post_utc - (%(lead)s || ' minutes')::interval
  ORDER BY o.race_id, o.combination, o.fetched_at DESC
)
-- snap を pre_odds 昇順で pop_rank 付与 → chihou.race_results と結合 → S / E を作る
```
⚠️ `fetched_at` は **UTC**、`post_time` は **JST hhmm**。9時間ずらすのを忘れない（`AGENTS.md:1336-1342`）。
本番と同じオッズを見たい場合は `backend/src/services/chihou_odds_query.py:45 latest_odds_sql()` を import して使う（`DISTINCT ON` は 850ms かかるため LATERAL 形に置き換えた経緯が `:12-18` にある）。

**DoD**:
- [ ] 人気薄の主定義・従定義が §2.3 の手順で決まり、根拠の表が残っている
- [ ] `E` の分布が JRA / 地方それぞれで出ている（頭数ビン別）
- [ ] ベースライン (a)(b) の `(選択率, R1, R2, lift)` 曲線が VAL 期間で出ている
- [ ] 使用データが**すべて発走前オッズ**であることをスクリプト内のアサーションで機械的に保証している

**Stop rule**:
- 🔴 **ベースライン (a) が (b) に対して全選択率帯で優位でない場合** → 「市場複勝確率を足す」という設計の前提が崩れている。Phase 1 に進まず設計をやり直す。
- 🔴 `E` が頭数だけでほぼ決まる（頭数を条件付けた後の残差分散が小さい）場合 → レース側モデルの上積み余地が無い。**頭数ルール1本を商品化する案に切り替えて終了**する。

---

### Phase 1 — レース側モデル vs ベースライン

**目的**: 学習した分類器がベースラインを**同一選択率で**上回るかだけを見る。

**成果物**: `backend/scripts/chihou_seat_model_ab.py` / `backend/scripts/jra_seat_model_ab.py`

**設計**:
- 目的変数: §2.2 の `E`（主）。`chaos_classifier_v1` の「三連単払戻 ≥ 100,000円」（`build_chaos_dataset.py:480`）および「中央値×5」（同 `:85, 499-505`）は**使わない**。払戻額は頭数と組み合わせ数の関数であり、席が空いたかどうかとは別物。
- 特徴量: **オッズ以外**を主とする。
  - レース属性: `head_count` / `distance` / `is_turf` / クラス（`grade_code`）/ 場 / `race_num` / `is_handicap` / 新馬・障害フラグ
  - ⚠️ 「転入馬の頭数と割合」は **DB に直接の列が無い**（§10-7 で確認済み）。使うなら過去走履歴から導出する項目として別途定義すること。初版の特徴量リストからは外す
  - `place_slots`
  - オッズ由来は **`odds_top1` と `entropy_norm` の2本まで**に制限する（§7.2 の二重使用対策）。この2本を入れた腕と抜いた腕を必ず両方作る。
- 腕:

  | 腕 | 中身 |
  |---|---|
  | `base_a` | 期待空席数（学習なし） |
  | `base_b` | 頭数 × `odds_top1` ルール（学習なし） |
  | `m_noodds` | LightGBM・オッズ列なし |
  | `m_odds2` | LightGBM・`odds_top1` + `entropy_norm` を追加 |

- 検証: walk-forward。地方は四半期 vintage（`backend/scripts/chihou_darkhorse_wf_build.py` と同方式・既定 2024-07〜2026-07 の9四半期）、JRA は `backend/scripts/anagusa_top3_walkforward.py` の `fit_vintage` / `quarters` を import する。
- **`chaos_classifier` の作り直し**: 上記 `m_odds2` が採用に至った場合のみ、`backend/models/chaos_classifier_v1.txt` を置換する `v2` を作る。目的変数を `E >= 1`（＝ N番人気以下が1頭以上3着内）へ差し替え、`RaceFeatures` の docstring が「確定単勝オッズ（締切前最終値）」としている市場構造列を**発走前スナップショット**へ置き換える（`backend/src/betting/race_selector.py:70-77`）。

**DoD**:
- [ ] 4腕の `(選択率, R1, R2)` 曲線が同一図に載っている
- [ ] `m_*` が `base_a` / `base_b` の**両方**に対し、選択率 10% / 20% / 30% の3点すべてで R2 を上回り、CI が 0 を跨がない
- [ ] 四半期別の内訳が出ており、特定四半期に依存していない
- [ ] 較正（`P(E>=1)` の Brier / ECE）がベースラインより悪化していない

**Stop rule**:
- 🔴 **同一選択率でベースラインに勝てなければ即中止。** `chaos_classifier_v1` の fresh lift が 1.667 で手置きルールの 1.79 に負けている前例（§1.1 事実5・6）を繰り返さない。
- 🔴 `m_noodds` と `m_odds2` の差の大半がオッズ2列から来ている場合 → レース側にオッズ以外の情報が無い＝二重使用の退化。中止する。
- 🔴 特定四半期を抜くと結論が反転する場合 → n 過小。判断せず窓を貯める。

---

### Phase 2 — 馬側スコア合成と既存商品との同一母集団比較

**目的**: 3因子の積が、既存の穴系商品と**同じ母集団・同じ選択率**で recall を上回るかを見る。

**やること**:
1. 3因子を掛けて馬単位スコアを作る。レース側の `P(E>=1)`（または `E_hat`）× 人気薄内正規化 市場複勝確率 × 指数フィルタ通過フラグ。
2. **同一母集団比較の対照**:
   - 地方: `chihou_is_place_pick`（発走前6番人気以下 × 指数5位内 × top3_share<0.63 × 頭数8+、`chihou_select_place_picks` で最大2頭）／`chihou_is_place_bet`（断然人気R × 単勝≥10 × 指数3位内 × 頭数8+）。実測は `chihou_recommender.py:855-859` の 572R 採点結果
   - JRA: `jra_upset_axis_tier`（`buy_signal.py:264`）＋ 穴系バッジ（穴ぐさ / netkeiba≤3 / kichiuma≤3 / DM battle≤2、`jra_build_highodds_pick`）
3. 対照と**選択率とレース網羅率を一致させて**から R1 / R2 / 層化リフトを並べる。
4. 合成の重み（3因子を積にするか対数和にするか、フィルタを積にするか足切りにするか）は **Phase 1 終了時点で事前登録に固定**し、ここでは探索しない。

**DoD**:
- [ ] 同一選択率で R2 が対照を上回り、CI が 0 を跨がない
- [ ] 層化リフトが 5-8番人気 +1.95pt / 9-12番人気 +0.81pt の水準を満たす
- [ ] precision@k が対照より有意に悪化していない
- [ ] ROI を CI つきで報告している（**判定には使わない**が、対照より明確に悪ければ商品説明を変える必要がある）
- [ ] TEST 期間での一度きり評価を1回だけ実行し、台帳へ記録済み

**Stop rule**:
- 🔴 R2 が対照と同等以下 → 打ち切り。既存商品の方が単純なので、同等なら既存を残す。
- 🔴 層化リフトが合格水準を満たさない → 市場のエコーを再パッケージしているだけ。打ち切り。
- 🔴 セグメント別に切って初めて勝つ場合（全体では負け）→ **事後分割は判断に使わない**。打ち切るか、次の独立窓に持ち越す。

---

### Phase 3 — 前向き記録への接続

**目的**: 後ろ向きの数字を、上書きされない前向き記録で答え合わせできる状態にする。

**基盤（すでに稼働）**:

| 柱 | テーブル | 記録内容 | 状態 |
|---|---|---|---|
| 地方 | `chihou.place_pick_races` / `chihou.place_picks`（`backend/src/db/chihou_models.py:420, 498`） | **全出走馬**の指数・発走前オッズ・pop_rank。推奨が出なかったレースも記録 | 稼働中・572R 採点済み（2026-09-01 時点） |
| JRA | `keiba.hit_tier_races` / `keiba.hit_tier_picks`（`backend/src/db/models.py:1177-1212`） | **全出走馬**の `composite_index` / `index_rank` / `place_probability` / `out_probability` / `pre_win_odds` / `pre_place_odds` / `pop_rank` | 2026-08-15 稼働・集計未実施（🟡 `jra_pick_log_report.py` の実行実績は**要確認**） |

スナップショット時刻: 地方 T−6分（`chihou_place_pick_log.py:85`）/ JRA T−10分（`jra_hit_tier_log.py:95`）。

**やること**:
1. **まず既存記録の集計を回す。** `backend/scripts/jra_pick_log_report.py --start 20260815 --end <直近>` と `backend/scripts/chihou_pick_log_report.py`。JRA 側は一度も集計されていない可能性があり、Phase 2 の対照値を DB からではなくここから取れる。
2. 両テーブルは全出走馬を持つので、**新スコアを反実仮想として後付けで再計算できる**（レース側特徴はオッズ以外＝出走表から復元可能、馬側は `pre_win_odds` から復元可能）。新しいカラムを足さずに評価できるか先に確認し、足りない列があって初めて alembic を検討する。
3. 商品化する場合のみ、レース側スコアを snapshot 時に保存する列を追加する。⚠️ `backend/alembic/` は **`shared` 柱**。並列作業にせず単独 PR で先に入れる（`CLAUDE.md:76`）。rev-id は `--rev-id "$(date +%Y%m%d%H%M)_<柱>"`。

**DoD**:
- [ ] 両柱の前向き記録が集計され、Phase 2 の対照値が前向きデータでも再現している
- [ ] 新スコアの反実仮想が前向き記録上で計算でき、後ろ向きの結論と符号が一致している
- [ ] 商品化する場合、`rule_version` 相当の署名を切って「どの閾値で出した札か」が後から判別できる

**Stop rule**:
- 🔴 前向き記録で後ろ向きの結論が再現しない → 後ろ向き側に look-ahead が残っている。原因を特定するまで配線しない。
- 🔴 標本が足りない（数百件に届かない）→ 判断せず貯める。`chihou_pick_log_report.py:19-21` の注意書きに従う。

---

## 5. データ要件と制約

### 5.1 発走前オッズの可用期間（🔴 ここが全体の律速）

| 柱 | テーブル | 券種 | 蓄積開始 | 出典 |
|---|---|---|---|---|
| JRA | `keiba.odds_history`（`backend/src/db/models.py:764-785`） | 7種（win/place/bracket/quinella/wide/exacta/trio/trifecta） | **2026-03-28〜** | `docs/jra_rebuild_2026_08.md:358`（60,368,822行/13GB、開催40日分） |
| 地方 | `chihou.odds_history`（`backend/src/db/chihou_models.py:298-317`） | **win / place の2種のみ** | **2026-04-07〜** | `docs/chihou_rebuild_2026_08.md:383`（68,455,423行/9,943MB）。`CLAUDE.md:1837`「それ以前は恒久的に補完不可」 |

- `win` / `place` は `prune_odds_history.py` の `KEEP_BET_TYPES` で保護されており刈られない（`backend/scripts/prune_odds_history.py:80, 87-88`）。exotic は 21日保持（`--exotic-keep-days`）。**本計画は win/place しか使わないので prune の影響を受けない。**
- 上記の行数・開始日は 2026-08 時点の台帳記録。実 DB の最小 `fetched_at` は 🟡 **要確認**（`SELECT min(fetched_at) FROM keiba.odds_history WHERE bet_type IN ('win','place')`）。
- 🟡 地方の `odds_history` には刈り込みスクリプトが存在せず、年 +30GB ペースで増える課題が残っている（`docs/chihou_rebuild_2026_08.md:385-387, 477`）。本計画で大量スキャンをかける前に容量を確認すること。

**帰結**: 発走前オッズを必須とする Phase 0〜2 の**評価窓は、JRA 2026-03-28以降 / 地方 2026-04-07以降に限られる**。約5か月分。これは §3 の CI を出すには薄い。したがって:

- **Phase 0 のラベル分布集計だけは、確定オッズを使わない部分（頭数・クラス・場と着順のみ）を全期間で出してよい。** ただし人気薄の定義にオッズが要るので、pop_rank を使う集計はすべて発走前窓に限る。
- Phase 1・2 の**採否判断は必ず発走前窓のみ**。窓が足りなければ **判断を先送りして貯める**。無理に確定オッズで代替しない（§7.1）。

### 5.2 検証窓（TRAIN / VAL / TEST）

**正本は `backend/src/jra_protocol.py` と `backend/src/chihou_protocol.py`。数値をスクリプトにハードコードせず import すること。**

| 柱 | TRAIN_END | VAL | TEST_START | ローリング | 台帳 |
|---|---|---|---|---|---|
| JRA | `20250630` | `20250701` 〜 TEST前日 | 当四半期の初日 | **四半期** | `backend/scripts/JRA_TEST_USAGE_LEDGER.md` |
| 地方 | `20250630` | `20250701` 〜 TEST前日 | 当月1日 | **月次** | `backend/scripts/CHIHOU_TEST_USAGE_LEDGER.md` |

再現性のため `JRA_TEST_START` / `CHIHOU_TEST_START` 環境変数で固定できる。

⚠️ **TRAIN_END(20250630) は発走前オッズの蓄積開始(2026-03/04)より前**。つまり「TRAIN でオッズ特徴を学習する」ことが構造的にできない。対処:
- レース側モデルは**オッズ以外の特徴で学習**する（そもそも §4 Phase 1 の設計がそうなっている）。これが本設計の副次的な利点。
- 市場複勝確率は**学習せず変換式で作る**（Henery λ は既存の凍結値 0.82 を使い、本計画で推定し直さない）。
- `odds_top1` / `entropy_norm` を入れる腕 `m_odds2` は、**発走前窓の中で train/eval を切る**（例: 2026-04〜06 学習 / 2026-07〜 評価）。この腕だけ窓が別であることを事前登録に明記する。

### 5.3 TEST 台帳の消費計画

🔴 **TEST は各判断につき1回のみ。使ったら `record_test_usage()` を呼ぶ。**

| 判断 | 柱 | 使う窓 | 消費 |
|---|---|---|---|
| Phase 0 の人気薄定義の決定 | 両 | **VAL のみ**（TEST を使わない） | 0 |
| Phase 1 のモデル採否 | 両 | VAL で探索。結論確認に **TEST 1窓** | 各柱1回 |
| Phase 2 の商品採否 | 両 | **TEST 1窓**（Phase 1 とは別の窓） | 各柱1回 |
| Phase 3 の配線 | 両 | 前向き記録のみ（TEST 不使用） | 0 |

- JRA は四半期ローリングなので、Phase 1 と Phase 2 の間に**四半期をまたぐ**必要がある。四半期をまたいで数字を直接比較しない（開催地が総入れ替わりになる）。
- 地方は月次なので回転は速いが、**1窓の合否で決めない**。競輪の `MIN_TEST_WINDOWS = 4` の思想を借り、**地方は最低3窓の合議**で採否を決めることを事前登録に書く。
- 台帳への追記は `jra_protocol.record_test_usage(decision, script, note)` / `chihou_protocol.record_test_usage(...)` を呼ぶだけでよい（自動追記される）。

---

## 6. 事前登録項目（実行前に固定するもの）

形式は `docs/jra_exotic_ev_preregistration_2026_08_23.md` に倣う。ファイル名案 `docs/upset_seat_decomposition_preregistration_<YYYY_MM_DD>.md`。
**結果を見る前に書き、実行後は本文を書き換えない（「結果」節に追記するだけ）。**

以下をすべて数値・式で固定すること。1つでも空欄があれば実行しない。

1. **母集団** — 柱・期間（開始日と終了日）・場の除外・`head_count` 下限・取消/除外の扱い・`place_slots > 0` 条件
2. **人気薄の主定義と従定義** — §2.3 の決定手順の**出力**（`P0` の値、またはオッズ閾値）
3. **ラベル** — `E` の作り方、主目的変数（`E>=1` か順序回帰か）
4. **発走前オッズのリード時間** — 地方 T−6分 / JRA T−10分（既存の前向き記録と揃える）。振らない
5. **特徴量リスト** — 腕ごとに列名を全部列挙。オッズ由来は 2 本まで
6. **λ（Henery）** — 0.82（`finish_order.get_lambda_params()` の既定）を使う。評価期間で推定し直さない
7. **指数フィルタ** — どれを使うか1本に固定。「フィルタなし」腕も必ず並べる
8. **合成式** — 積か対数和か。フィルタは積か足切りか
9. **比較対象** — `base_a` / `base_b` / 既存商品（地方 `chihou_is_place_pick` + `chihou_is_place_bet` / JRA `jra_upset_axis_tier`）
10. **選択率** — 比較する固定点（10% / 20% / 30%）。ここ以外の点は報告してよいが採否に使わない
11. **主判定基準** — 「選択率 10/20/30% の全点で R2 が対照を上回り、レース単位ブートストラップ CI が 0 を跨がない」
12. **従判定基準** — 層化リフトが 5-8番人気 +1.95pt / 9-12番人気 +0.81pt を満たす
13. **頑健性** — 四半期（地方は月）別の内訳を出し、単一窓に依存しない
14. **窓の使い方** — 各判断でどの窓を TEST として消費するか。地方は最低3窓の合議
15. **stop rule** — §4 の各 stop rule をそのまま転記
16. **明示的に否定するもの** — §1.3 の表を転記。特に「本計画は `jra_race_level_walkforward.py` が否定した『レース単位特徴を馬側モデルへ配る』設計**ではない**」ことを1文で書く

---

## 7. リスクと既知の落とし穴

### 7.1 確定オッズ禁止（最重要）

過去2回の崩壊を必ず参照すること。

| 事故 | 内容 | 出典 |
|---|---|---|
| ① 指数の look-ahead | 「複勝圏率 51.5%（母集団の4.36倍・p=1.0e-22）」が honest には **28.2%（×2.36）** だった | `docs/chihou_rebuild_2026_08.md:623, 628, 681, 1059` |
| ② 選別オッズの差替え | 波乱度×高配当帯の三連単 ROI が確定オッズ選別 **0.804 [0.691, 0.923]** → 発走6分前選別 **0.512 [0.301, 0.796]**。単調性も消えた | `backend/docs/chihou_exotic_type_lab_2026_08_29.md:20, §10` |

②の乖離の内訳が特に重要:
- 波乱度の相関 0.788 だが、**五分位の一致率は 51.5%**
- 200倍帯で選ぶ10点の重なりは **中央値で10点中1点**

→ **「発走前と確定オッズはほとんど別の商品を作る」。** 相関が高いことは代替可能性を意味しない。本計画のレース側スコアも波乱度と同じ性質を持つので、**確定オッズで一度でも測ったら、その数字は報告に載せない**（載せるなら「確定オッズ・参考・実運用不能」と明記する）。

対策として、Phase 0 のスクリプトに **オッズ取得が `post_utc − lead` 以前であることのアサーション**を入れ、テストで固定する。

### 7.2 オッズの二重使用による退化

レース側 `P(E>=1)` と馬側の市場複勝確率が**どちらも単勝オッズから作られる**と、積が「オッズの2乗」に退化し、市場のエコーを増幅するだけになる。

対策:
- レース側の特徴を**オッズ以外**（頭数・クラス・場・新馬/障害）で主構成する
- オッズ由来は `odds_top1` と `entropy_norm` の 2 本まで
- `m_noodds` 腕を必ず並べ、**オッズ2列を抜いても効くこと**を確認する（Phase 1 stop rule）
- 層化リフト（§3.2）は人気を揃えるので、エコーが正体なら 0 に潰れる。これが検出装置になる

### 7.3 人気割れレースでは ROI が上がらない構造

「席が空きやすいレース」= 市場が割れているレース = **穴のオッズが圧縮されているレース**。的中率は上がっても払戻が同時に下がるため、ROI は改善しない可能性が高い。実際 `chihou_exotic_type_lab` §10 では、良く見える側が「荒れ」ではなく**「堅い」側（上位3頭シェア大・1番人気強）に反転**していた。

→ §1.2 の「的中率商品」宣言はこの構造への対応であって、逃げではない。**商品説明でも「当たりやすくなるが儲かるとは限らない」を明記する。**

### 7.4 n 過小のセグメント判断禁止

- 発走前オッズの窓が約5か月しかない（§5.1）
- `chihou_exotic_type_lab` §10 は 139日・的中28件で **CI ±0.25**。この幅では何も否定も肯定もできない
- 事後にセグメントを切って勝った腕は採らない。台帳の前例（`jra_thin_career_head_walkforward.py` のキャリア0-2走×1-4番人気 +1.31pt に「事後分割かつ多重比較なので次窓での確認を必須」と付いている）に倣う

### 7.5 単変量の有意性 ≠ モデルへの増分

`docs/chihou_rebuild_2026_08.md:17.7` の教訓。血統は out-of-time 単変量で +1.44pt / 3.0σ だったが、walk-forward A/B の増分はゼロだった。

→ 本計画でも「レース側の変数が単独で `E` と相関する」ことを採用根拠にしない。**必ずベースラインに対する増分**で判断する（Phase 1 の設計がこれ）。

### 7.6 検証の作法（`CLAUDE.md:195-240`）

実験設計前に本番コードから以下を書き出す:
- 買い目点数・賭け金配分・母集団・ゲートの例外・学習窓/評価窓の分離・ユーザー提案を近似していないか

> **定数の存在は使われている証拠にならない。呼び出し元を辿ること。**
> **「変わりうる」と「変わっている」は別。測ってから言う。**

`chaos_classifier_v1` がまさにこの例（モデルファイルも推論関数もあるが `race_selector.py:3-5` の docstring どおり本番未配線）。

### 7.7 その他

- **DB の `calculated_indices` / `composite_index` で過去を評価しない。** 当日 21:30 JST に確定オッズ入力で再算出・上書きされる（`chihou_models.py:423-427`）。honest 評価は walk-forward か前向き記録のみ。
- **四半期/月をまたいで数字を直接比較しない。** JRA は開催地が総入れ替え、地方は季節性が強い。
- 地方の `race_results.place_odds` は欠損が着順と相関する構造バイアスがある（`docs/chihou_rebuild_2026_08.md:87, 467`）。ROI を出すときは NULL 行を黙って落とさない。

---

## 8. 実装タッチポイント

### 8.1 既存ファイルとの関係

| ファイル | 柱 | 本計画での扱い |
|---|---|---|
| `backend/src/betting/race_selector.py` | **shared** | Phase 1 で採用に至った場合のみ、`FEATURES` と `RaceFeatures` を発走前オッズ前提へ差し替え。`betting/` は shared なので**単独 PR・並列禁止**（`CLAUDE.md:76`） |
| `backend/models/chaos_classifier_v1.txt` | — | 置換せず `chaos_classifier_v2.txt` を新規に作る。v1 は「三連単払戻」目的で残す |
| `backend/scripts/build_chaos_dataset.py` / `train_chaos_classifier.py` / `evaluate_chaos_classifier.py` | jra | 目的変数と特徴量が変わるので**流用ではなく新規スクリプト**。ただし `_lift_table()` と `simple_rules` の比較設計（`evaluate_chaos_classifier.py:94-160`）はそのまま踏襲する |
| `backend/src/indices/confidence.py`（`calculate_market_chaos` / `ENTROPY_THRESHOLDS`） | jra | **import して使う。再実装しない。** entropy_norm は JRA 本番配線済み。🟡 地方側の entropy_norm 採否は**要確認**（研究スクリプト `chihou_entropy_norm_analysis.py` は存在するが、採用の記録が `docs/chihou_rebuild_2026_08.md` に見つからない） |
| `backend/src/betting/finish_order.py`（`_henery_adjusted` / `_place_prob_single`） | shared | **import して使う。** λ は `get_lambda_params()` の凍結値 |
| `backend/src/indices/buy_signal.py`（`chihou_is_place_pick` / `chihou_is_place_bet` / `jra_upset_axis_tier`） | jra（ファイル自体） | Phase 2 の**対照として import**。ロジックを触らない |
| `backend/src/indices/chihou_upset.py` / `upset_reranker.py` | chihou / jra | 指数フィルタ候補として import |
| `backend/src/services/chihou_odds_query.py:45 latest_odds_sql()` | chihou | 発走前オッズ取得の正本。**再実装しない** |
| `backend/src/services/chihou_place_pick_log.py` / `jra_hit_tier_log.py` | chihou / jra | Phase 3 の接続先。`SNAPSHOT_LEAD_MINUTES` は 6 / 10 |
| `backend/scripts/chihou_pick_log_report.py` / `jra_pick_log_report.py` | chihou / jra | Phase 3 でまず実行する。JRA 側は未集計の可能性 |
| `backend/scripts/anagusa_top3_walkforward.py`（`strat_diff` / `boot_mean` / `fit_vintage` / `quarters`） | jra | **import して使う**（`jra_darkhorse_walkforward.py:60-71` が前例） |
| `backend/scripts/chihou_darkhorse_wf_build.py` | chihou | 地方の walk-forward CSV 生成。既定 2024-07〜2026-07・9四半期 |
| `backend/src/jra_protocol.py` / `chihou_protocol.py` | jra / chihou | 窓定数を import。`record_test_usage()` を必ず呼ぶ |

### 8.2 柱分類と worktree 運用

判定の唯一の情報源は `scripts/dev/pillars.sh` の `pillar_of`。評価順は **shared のパス列挙 → `*chihou*` キーワード → jra フォールバック**。

- `backend/scripts/chihou_*.py` → **chihou 柱**（ファイル名にキーワードが入るため）
- `backend/scripts/jra_*.py` → **jra 柱**
- `backend/src/betting/*` / `backend/alembic/*` / `backend/src/db/models.py` → **shared 柱**

**鉄則（`CLAUDE.md:99-110`）**:
1. **`main` では作業しない。** branch protection で機械的に強制されており直接 push できない。変更は必ず PR 経由
2. **1 ブランチ = 1 柱。** 本計画は JRA と地方の両方に触るので、**必ず柱ごとにブランチを分ける**
3. **`shared` を触る作業は並列にしない。** 単独 PR で最優先に main へ入れ、他ブランチはその後 rebase
4. Alembic を並列生成しない。rev-id は `--rev-id "$(date +%Y%m%d%H%M)_<柱>"`

**worktree の作り方**（置き場所は `.worktrees/` ではなく **`../kiseki-wt/<柱>/<トピック>`**）:
```bash
bash scripts/dev/wt.sh new chihou upset-seat      # → ../kiseki-wt/chihou/upset-seat / feat/chihou-upset-seat
bash scripts/dev/wt.sh new jra    upset-seat      # → ../kiseki-wt/jra/upset-seat    / feat/jra-upset-seat
```
そのフォルダで**別の** Claude Code セッションを起動して作業する。着手前に `bash scripts/dev/scan_collisions.sh`、コミット前に `/pd-preflight`。

コミット/PR は履歴上の慣習に従う（明文化された規約は無い）: `<type>(<柱>): 日本語の要約 (#PR番号)`。例 `feat(chihou): レース波乱度の席数ラベルを集計するスクリプトを足す (#NNN)`。

### 8.3 ブランチ分割案

| ブランチ | 柱 | 中身 |
|---|---|---|
| `feat/chihou-upset-seat` | chihou | `chihou_upset_seat_label.py` / `chihou_seat_model_ab.py` |
| `feat/jra-upset-seat` | jra | `jra_upset_seat_label.py` / `jra_seat_model_ab.py` |
| `feat/shared-race-selector-v2` | shared | `race_selector.py` の差し替え（**Phase 1 採用後のみ・単独 PR**） |
| `docs/shared-upset-prereg` | shared | 事前登録文書（`docs/` 配下・`CLAUDE.md` 更新を伴う場合は shared） |

---

## 9. スケジュール目安と判断ポイント

工数は 1 セッション ≒ 半日として概算。

| # | 内容 | 目安 | 判断ポイント |
|---|---|---|---|
| S1 | 事前登録の起草（§6 の16項目のうち、Phase 0 で決まる 2 以外を埋める） | 1 | — |
| S2 | Phase 0: 地方のラベル分布 + ベースライン2本 | 2 | 🔶 **人気薄の定義を確定**（以後動かさない） |
| S3 | Phase 0: JRA 同上 | 1 | 🔶 **Phase 0 stop rule 判定**。頭数だけで決まるなら頭数ルール1本に切り替えて終了 |
| S4 | 事前登録の確定（項目2を埋めて凍結）→ `docs/` へコミット | 0.5 | 🔴 **凍結後は本文を書き換えない** |
| S5 | Phase 1: 地方の 4 腕 walk-forward A/B（VAL） | 2 | — |
| S6 | Phase 1: JRA 同上（VAL） | 2 | — |
| S7 | Phase 1: TEST 1窓で確認 + 台帳記録 | 0.5 | 🔴 **Phase 1 判定**。ベースラインに勝てなければここで終了 |
| S8 | Phase 2: 合成スコアと既存商品の同一母集団比較（VAL） | 2 | — |
| S9 | Phase 2: TEST 1窓（Phase 1 と別窓）+ 台帳記録 | 0.5 | 🔴 **Phase 2 判定＝商品化の可否** |
| S10 | Phase 3: 前向き記録の集計（まず既存の `jra_pick_log_report.py` を回す） | 1 | 🔶 後ろ向きの結論が前向きで再現するか |
| S11 | Phase 3: 配線（採用時のみ） | 2〜 | — |

### 期間の制約

- JRA は**四半期ローリング**なので、S7 と S9 の間に四半期をまたぐ必要がある。TEST_START が 2026-10-01 に切り替わるため、**S7 を 2026Q3 窓で実行するなら S9 は 2026Q4（2027-01 以降）**になる。JRA 側は数か月単位の待ちが入る。
- 地方は**月次ローリング**で回転が速いが、**最低3窓の合議**を課すので S7 → S9 に最短でも3か月。
- 発走前オッズの窓が薄い（§5.1）ため、**待つこと自体が n を増やす**。急いで薄い窓で判断するより、待って判断する方が期待値が高い。

### 撤退条件（プロジェクト全体）

以下のいずれかに触れたら**プロジェクトを畳み、結論を `docs/` と台帳に残して終了する**:

1. Phase 0 で `E` が頭数でほぼ決まると判明した（→ 頭数ルール1本を商品化する提案に縮退）
2. Phase 1 で学習モデルが同一選択率のベースライン2本に勝てなかった
3. Phase 2 で R2 が既存商品と同等以下だった
4. 層化リフトが合格水準（5-8番人気 +1.95pt / 9-12番人気 +0.81pt）に届かなかった

畳む場合も、**Phase 0 の期待空席数ベースラインは単体で価値がある**（学習不要・オッズだけ・レース選別に使える）ので、それだけを軽い商品として残す選択肢を検討する。

---

## 10. 未確認事項 — 2026-09-02 に全件解消

着手前に潰すこととしていた7件を、本番 DB とコードで確認した。**以下はすべて実測**。

| # | 項目 | 結果 |
|---|---|---|
| 1 | `odds_history` の実際の最小 `fetched_at` | **台帳どおり**。`keiba` win `2026-03-28 01:38` / place `2026-03-28 02:10`（各 833万 / 832万行）、`chihou` win/place とも `2026-04-07 04:43`（各 3,984万 / 3,958万行）。最大は keiba `2026-08-30`・chihou `2026-09-02` |
| 2 | 地方の `entropy_norm` は本番採用されているか | **未採用で確定**。`entropy_norm` / `calculate_market_chaos` の参照は `api/races.py`・`services/jra_race_confidence.py`・`jra_hit_tier_log.py`・`services/recommender.py` の JRA 系のみ。`chihou_*` の本番コードに参照ゼロ（研究スクリプト `chihou_entropy_norm_analysis.py` だけが存在）。**地方で使うなら新規配線が要る** |
| 3 | `jra_pick_log_report.py` の実行実績 | **記録は貯まっていた**（`keiba.hit_tier_races` 216レース・**全件 settled**・20260815〜20260830・6開催日）。集計は 2026-09-02 に**初めて実行**した（結果は §11.1） |
| 4 | `chihou.place_picks` の採点済レース数 | **703レース記録 / 681 settled**（20260814〜20260902）。計画起草時に引いた 572R から増えている |
| 5 | 地方 `odds_history` の容量 | `chihou.odds_history` **11 GB** / `keiba.odds_history` **13 GB**。大量スキャンは可能だが、`win`/`place` に絞り期間を切ること |
| 6 | 地方 races の JRA 除外条件 | **`WHERE course != '83'` が正しい**（`chihou_entropy_norm_analysis.py:128`）。実データでも `course='83'` は `course_name` が `'83'` のまま解決されておらず、2026-04-07 以降で 744レース混在している。除外必須 |
| 7 | JRA 側に「転入馬」相当の列があるか | 🔴 **中央・地方とも直接の列は存在しない**。`race_entries` は両スキーマとも `east_west_code` を持つのみ（keiba 18列 / chihou 16列を全件確認）。**§4 Phase 1 の特徴量から「転入馬の頭数と割合」を削除し、使うなら過去走履歴からの導出項目として別途定義する**こと |

---

## 11. 2026-09-02 に測った現在地（計画の前提を更新する）

§10-3 / §10-4 の集計を実行した結果、**計画起草時に想定していなかった対照が2つ見つかった**。事前登録の §6-9（比較対象）はこれを含めて書き直すこと。

### 11.1 JRA hit_tier 前向き記録の初集計（216R・6開催日・20260815〜20260830）

`rule_version: hit_tier,gap=6.0,entC=0.7757,cut=0.8` / 撮影リード中央値 9分前。

| tier | n | 的中率 | 券種 |
|---|---|---|---|
| S | 35 | 54.3% | 単勝 |
| A | 32 | **28.1%** | 単勝 |
| B | 45 | 53.3% | 複勝 |
| C+ | 27 | 25.9% | 複勝 |
| C（棄権） | 77 | — | — |

- 棄権77レースの指数1位馬: 勝率 18.2% / 複勝率 36.4%（＝見送りは概ね正しい向き）
- 反実仮想（tier を問わず指数1位を買う）: n=214・勝率 27.6%・複勝率 **47.7%**・単勝ROI **0.805**
- 🔴 **発走前 tier と確定オッズ tier の一致は 91.7%（198/216）**。8.3% は確定オッズで測ると別 tier になる。§7.1 の「確定オッズ禁止」を裏づける本番実測がこれで手に入った

⚠️ tier A が 28.1% と、CLAUDE.md 記載の in-sample 値（33〜40%）を下回る。ただし n=32 で CI は極めて広く、**6開催日では判断しない**（§7.4）。

### 11.2 🔴 地方の現行ゲートは、既に粗い「席が空くか」モデルである

`chihou_pick_log_report.py` の「棄権の答え合わせ」節が、**本計画のレース側 KPI そのものを前向きに測っていた**。

| 区分 | レース数 | 人気薄が複勝圏に来た率 |
|---|---|---|
| 棄権 | 566 | **46.1%** |
| 推奨あり | 115 | **70.4%** |

現行の棄権理由の内訳は `closed_race 71.0%` / `small_field 7.8%` / `no_candidate 3.4%` / `no_odds 1.1%`。`closed_race` は **上位3頭のオッズシェア `top3_share >= 0.63`** による除外であり、これは「上位人気が席を埋めてしまうレースを外す」という**本計画のレース側モデルの粗い実装に他ならない**。

**帰結（事前登録に反映すること）**:

1. **ベースラインを3本にする。** §4 Phase 0 の `base_a`（期待空席数）・`base_b`（頭数×`odds_top1`）に加え、**`base_c` = 現行ゲート（`top3_share < 0.63` ∧ 頭数8+）**を必ず並べる。前向きで lift **1.53**（70.4% / 46.1%）を出している対照を無視して「勝った」と言ってはいけない
2. **`E>=1` の base rate は約 50%**（681レースからの逆算で 50.2%）。ラベル密度は十分だが、**レース選別の lift 上限は 2.0 で構造的に頭打ち**。§3.1 の R2 を上げる主経路は選別ではなく「選んだレースの中で何頭に札を出すか」側にある可能性が高く、Phase 0 でこの分解を先に出すこと
3. 🔴 **現行商品のレース単位 recall は約 10.5%** であって、計画 §1.1 事実2 に引いた **31.9% ではない**。31.9% は「全レースで指数5位内を見た場合」の値で、現行ゲートは 71% のレースを捨てている（推奨あり115R のうちレース的中 36R ÷ 人気薄が複勝圏に来た全 342R ≒ 10.5%）。**両者は母集団が違う。R2 を報告するときは必ずゲート込みかゲート無しかを明記する**

### 11.3 地方 注目馬の現在値（Phase 2 の対照）

n=165・複勝率 **22.4%（±3.2pt）**・複勝ROI **0.858**・発走前単勝オッズ中央値 16.1倍。レース的中 31.3%（115R中36R）。
カバレッジは開催752R中703R記録（93.5%）、推奨が出たのは 117R（16.6%）。

反実仮想の運用点比較（**事後の比較であり乗り換えの根拠にはしない**・現行は指数5位内×最大2頭）:

| ルール | 推奨頭数 | 複勝率 | レース的中 |
|---|---|---|---|
| 指数3位内 × 最大1頭 | 54 | 29.6% | 29.6% |
| 指数3位内 × 最大2頭 | 59 | 28.8% | 31.5% |
| **指数5位内 × 最大2頭（現行）** | 167 | 22.2% | 31.3% |
| 指数6位内 × 最大2頭 | 229 | 18.8% | 29.9% |

→ **レース的中は 29.6〜31.5% でほぼ横ばい**の一方、複勝率は絞るほど上がる。これも「選別より頭数配分」を示唆する（11.2-2 と同じ向き）。

---

*本文書の §1〜§9 は計画であり実測結果を含まない。数値はすべて既存文書・既存コード・既存アーティファクトからの引用で出典を併記している。§10・§11 のみ 2026-09-02 に本番 DB とコードで実測した結果で、これは事前登録前の現在地把握であって TEST 窓の消費ではない（前向き記録と VAL 以前の集計のみを見ている）。*
