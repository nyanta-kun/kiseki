# 地方競馬モデル設計監査 — 配当捕捉のための妥当性レビュー

対象: chihou v9(線形) → v10/v11(LightGBM) + アンサンブル構成
監査日: 2026-06-04 / 監査対象コード: `src/indices/chihou_calculator.py`, `scripts/train_chihou_v10_lightgbm.py`, `scripts/train_chihou_v11_lightgbm.py`, `scripts/inference_chihou_v10.py`, `src/services/chihou_recommender.py`, `src/indices/buy_signal.py`

---

## ① 現設計の正確な記述

### 1-1. 2段構成（サブ指数 → ラッパー）
**第1段: v9 線形（乗数）モデル** (`chihou_calculator.py::calculate_and_save`)
- 5サブ指数を独立バッチ算出: `speed_index, last3f_index, jockey_index, rotation_index, last_margin_index`（各 平均50/SD10 の z-score → index 換算、欠損時は `INDEX_NEUTRAL=50.0`）。
- composite を**加重平均ではなく乗算**で合成（v7以降）:
  ```
  ability_base = (0.45·speed + 0.20·last3f + 0.35·last_margin) / Σw   # last_margin NULL時は speed/last3f の2指数で正規化
  composite    = clip( ability_base × K_jockey × K_rotation × K_dark × K_margin )
  ```
  - `K_jockey` = `1 + (jockey-50)/50 × max_jk`（コース別 max、±50〜80%）
  - `K_rotation` = ±8%固定、`K_dark` = [1.00,1.20]（前走後方・距離短縮・外枠）、`K_margin` = [1.00,1.08]
- 確率化: `win_probability = softmax(composite, T=10.0)`（レース内で和=1）、`place_probability = Harville(win_probs)`（上位3着以内、和≈3）。
- **重要**: このコードは `version = CHIHOU_COMPOSITE_VERSION = 10` で書き込む。つまり「v9計算ロジック」がDB上は**version=10タグ**で保存される。

**第2段: v10 LightGBM + アンサンブル** (`scripts/inference_chihou_v10.py`、バッチ専用)
- 入力 = 5サブ指数 + レースメタ(distance, head_count, is_turf/is_dirt, is_good/is_heavy/is_bad) + 馬メタ(frame_number, horse_age, weight_carried, horse_weight, weight_change) の計17特徴量。
- 目的変数 = **3着以内binary**（`objective=binary`, `binary_logloss`）。
- LGB 生スコアを**レース内 min-max → 15〜85 にスケール**（`scale_to_index`）。
- アンサンブル: `composite = 0.3 × scale(lgb) + 0.7 × v9_composite`（`lgb_weight=0.3, linear_weight=0.7`、JRA v26と同比率を流用）。
- `win_probability` = **lgb_index（15-85）単独の softmax(T=10)**。Harville × 3 clip で place。

### 1-2. v11（実験）
v10の17特徴量 + 4つの履歴特徴量（`improving_form, track_win_rate, class_drop_ratio, prev_pace_ratio`）。本番未投入。

### 1-3. なぜ 0.3/0.7 か
コード/docstringいずれにも**地方固有の根拠はなく**「JRA v26 と同比率」と明記（`inference_chihou_v10.py` L7, L208-209）。地方でのアンサンブル比のグリッド探索・複数seed検証の痕跡は無い。**根拠の借用**であり、地方データで最適化されていない。

### 1-4. 本番で実際に効いているもの（実測で判明した重大事実）
DBを直接確認した結果:
- **直近本番（2026-05-20〜06-01）には version=10 しか存在せず version=9 が無い**（v10=5355行, v9=0行）。`win_probability` はレース内で和=1.0 の綺麗な softmax。
- 一方バックテスト窓（2026-01〜04）は v9=40,597 / v10=40,175 と両方存在。
- → **realtime取込パス（`chihou_import_router.py::calculate_and_save`）は v9計算を version=10 として直接書き込むだけ**で、LGBアンサンブル(`inference_chihou_v10.py`)は cron/LaunchAgent にも無く、本番パイプラインに組み込まれていない。しかも inference は version=9 行を入力に要求するが本番ではその行が生成されないため、**仮に走らせても直近レースには適用不能**。

> **結論**: 「v10 LightGBM アンサンブルが本番」というのは事実上 **過去バックテスト窓だけの状態**。recommender/`buy_signal` が読む直近の `win_probability` は **v9線形のsoftmax**であり、LGBは推論経路に存在しない。診断オーケストレータが観測した「v10」指数1位の劣化も、実体はほぼ v9線形の挙動。

---

## ② リーク / バグ点検結果

### 2-1. 既知バグ（確認: 真）— `train_chihou_v11_lightgbm.py` L479-480
```python
for d in [df_train, df_valid, df_test]:
    d["win_probability"] = d["y"]   # 暫定（evaluateの内部EV計算用）
```
`win_probability` に**正解ラベル y（3着以内=1）を代入**。`evaluate()` 内の `ev = win_probability × win_odds`（L295）が「ラベル×オッズ」になり、`ev_filter_roi` は完全に無意味（リークしたメトリクス）。**v11の特徴量採否がこの偽メトリクスで判断されていた恐れ**。学習自体（X, y）には混入しないが、意思決定指標が壊れている。→ v11評価は全てやり直し必須。

### 2-2. 推論系の "win_probability ≠ composite" 不整合（新規発見・重大）
`inference_chihou_v10.py`: `composite_index` は `0.3·lgb + 0.7·v9` のアンサンブルだが、`win_probability` は **lgb_index 単独**の softmax から算出（L240-247、v9を混ぜていない）。
- つまり**表示indexと確率が別物**。`buy_signal.chihou_is_sweet_spot` / `place_bet` は `win_probability × win_odds` の EV で判定するので、**アンサンブルの恩恵を全く受けていない確率**で馬券判断している。
- さらに recommender が読むのは（①-4の通り）v9-softmaxの win_probability。**「v10アンサンブルでROIを実証した」という前提が推論実装と一致していない**。

### 2-3. キャリブレーション欠如
binary 出力（生）も lambdarank 出力も、`scale_to_index`（min-max）→ `softmax(T=10)` を通すだけ。**isotonic/Platt等の確率較正は一切なし**。min-maxはレース毎にレンジが伸縮するため、頭数・混戦度で同じ生スコア差が違う確率に化ける。EVゲート（sweet_spot）がこの未較正確率に依存している。

### 2-4. 学習/評価のリーク点検（その他は概ね健全）
- v10 train: 期間分割は日付で時系列ホールドアウト（OK）。サブ指数は前走由来でリークなし。
- v11 履歴特徴量は `shift(1)`/cumsum-自走分 で現走前のみ参照（リークなし、実装は妥当）。
- ただし **v10/v11ともに単一seed**。CLAUDE運用ルール「複数seed平均で判定」に違反。num_iterations だけ early_stopping だが seed・drop1・CI検証の痕跡なし。

---

## ③ 目的変数・キャリブレーション・アンサンブルの妥当性評価

### 3-1. 目的変数: 3着以内binary は配当捕捉に最適か → **NO（部分的に不適）**
- 現状の運用は「指数1位の単勝」「sweet_spot=単勝」「place_bet=複勝」と**買い目が混在**するのに、学習目的は **top3 binary 一本**。単勝を当てたいのに top3 を最適化しているため、3着候補（人気で堅実に来る馬）を1位に押し上げ、**勝ち切る穴**を取りこぼす方向のバイアス。
- 配当(=単勝/複勝の妙味)を狙うなら目的変数は買い目に整合させるべき:
  - 単勝狙い → `is_win`(1着) binary か lambdarank(rel=着順逆数)。
  - 複勝狙い → `is_top3` binary（現状）で良い。
  - **配当そのものを最大化したいなら回収率を意識したラベル/重み**（例: 1着サンプルを `log(win_odds)` で重み付け、または payoff回帰）が候補。ただし過学習リスク大→必ずOOS/drop1で検証。
- lambdarank は順位最適化として top3 binary より「1位の精度」に向くが、`rel=5−finish` の単純設計では中位の情報が薄い。`ndcg@1` を主指標にするなら検討価値あり。

### 3-2. キャリブレーション: レース内softmax正規化の妥当性 → **不十分**
- softmax(T=10) はランクをなだらかな確率に均す**ヒューリスティック**で、実勝率に較正されていない。実測（②-3関連）でも指数1位の平均勝率34.6%に対し softmax 上位確率は系統的に乖離しうる。
- 推奨: **out-of-fold で isotonic回帰**（または温度Tをvalidで最適化）し、「予測勝率=実勝率」を保証してからEVゲートに使う。EV閾値(1.0/1.2/2.0)はキャリブレーション済み確率前提でないと意味を持たない。
- min-max→softmaxの二重変換は情報を歪める。**生スコアを直接較正**し、レース内正規化は「確率を頭数で割って和=1」程度に留めるべき。

### 3-3. アンサンブル 0.3/0.7 → **根拠なし・要再最適化**
- JRAからの借用比率。地方は市場効率が異なるため最適比率は別。`w_lgb ∈ {0,0.2,..,1.0}` を**複数seed×OOS×drop1**で掃引すべき。
- そもそも②-2の通り**確率にはアンサンブルが反映されていない**ため、現状の0.3/0.7は表示indexの見栄えにしか効いていない。アンサンブルするなら「スコアレベルで混ぜてから1回較正」に統一すること。

### 3-4. 2段構成（サブ指数入力）vs 生特徴量直接学習 → **2段は情報ボトルネック**
- 5サブ指数は人手の重み・乗算式・clip・neutral補完を**通過済みの圧縮表現**。LGBがそこから学ぶと、サブ指数生成時に捨てた情報（生タイム、相手構成、通過順、距離変化の連続値など）を二度と回収できない。
- 特に jockey_index/last_margin は**点推定をindex化した時点で不確実性が消える**（後述④）。
- 推奨: **生特徴量（race_results由来の連続値・カテゴリ）から直接1段で学習**し、サブ指数は「強い事前特徴量の1つ」として併用する設計の方が表現力が高い。DBには未使用カラムが多数（running_style, passing_1..4, time_diff, last_4f, margin, prev_*）。

### 3-5. 5サブ指数のノイズ源性 → **jockey/rotation/last_marginが要注意**
neutral=50.0 が入る割合（直近1.5年, head≥6）:
- rotation 9.2%, speed/last3f 3.7%, last_margin NULL 3.7%, jockey 0.6%。
rotation の neutral率が高く、`K_rotation`(±8%) が初出走馬等で常に中立に張り付く=実質ノイズに近い。jockey/last_margin は欠損率は低いが**履歴薄時の点推定**が不安定（次節）。

---

## ④ 劣化の機構仮説と実測

### 観測された劣化（オーケストレータ + 本監査の独立実測, v10窓 2026-01〜04, head≥6）
- 指数1位 勝率 **34.6%** / 複勝 66.4% に対し、市場1番人気 勝率 **46.4%** / 複勝 77.7% → **市場に明確に劣る**。
- 勝ち馬の **32.6%** をモデルが rank4+ に降格（demote）。

### 仮説A検証: 「履歴が薄い馬でrank誤差が大きい」→ **棄却（逆だった）**
prior_runs（現走前のキャリア戦数）別の指数1位成績:

| prior_runs | n | 勝率 | 複勝率 | 平均人気順位 |
|---|---|---|---|---|
| 0-3 | 913 | **43.7%** | 73.2% | 1.54 |
| 4-8 | 1035 | 39.4% | 72.1% | 1.76 |
| 9-15 | 897 | 32.1% | 66.0% | 2.10 |
| 16-30 | 730 | 23.8% | 55.1% | 2.46 |
| 31+ | 229 | **21.0%** | 50.7% | 2.80 |

勝ち馬の降格率（winnerをrank4+に落とす）も:

| winner prior_runs | demote率 | 平均mrank |
|---|---|---|
| 0-3 | 21.8% | 2.51 |
| 9-15 | 32.6% | 3.21 |
| 31+ | **52.3%** | 4.30 |

→ **モデルは履歴の薄い馬でむしろ正確**（強い人気馬を素直に1位にする）。劣化は**ベテラン・多数出走馬（=低クラスの混戦）**に集中。

### 仮説B（採用）: 混戦・低クラスでサブ指数が圧縮 → softmaxがフラット化 → 市場の方が情報を持つ
- prior_runs が多い馬は南関下級の頻走馬が中心。そこでは出走全馬の speed/last3f が団子になり、ability_base の差が小さい→乗算後も差が出ず→**softmax(T=10)がほぼ一様**。結果、モデルは弱い差で適当に1位を選び、勝率が市場(46→21%)を大きく下回る。
- これは「能力推定が市場に劣る」のではなく、**サブ指数が混戦帯で解像度を失い、かつ確率化が均しすぎている**構造問題（③-4, ③-2 と直結）。

### 仮説C（採用・症状A本体）: 降格された勝ち馬の36%は市場favorite
demoted winners（モデルrank4+の勝ち馬, n=1238）の市場人気内訳:
- pop1-2: **36.4%** / pop3-5: 39.7% / pop6+: 23.9%、median win_odds **7.1**。

→ **「指数が低評価した勝ち馬」の1/3超は市場が正しく本命視した馬**で、極端な人気薄ではない（中位オッズ）。これは edge(オッズの隙間)ではなく**単なる能力推定の取りこぼし**。一方 pop6+ が24%含まれる点は、狙うべきedge候補が混在していることも示す（ただし大半は実力で来た本命）。

### 機構まとめ
劣化は「目的変数(top3)」「サブ指数の混戦帯解像度不足」「未較正softmaxの均し」の合成。**履歴の薄さは原因でない**。2段構成が混戦帯の生情報（着差・通過順・相手質）を捨てている点が根因。

---

## ⑤ 推奨モデル設計案（複数案比較）

| 案 | 構成 | 目的変数 | 確率較正 | 長所 | 短所/リスク | 工数 |
|---|---|---|---|---|---|---|
| **0. 即修正(必須)** | 現行のまま | 変更なし | — | ①推論パスでwin_probをアンサンブルと整合 ②v11のwin_prob=yバグ修正 ③本番にinference経路を組込む(or v9softmaxで運用と明記) | 設計は変えない | 小 |
| **A. 1段直接学習** | 生特徴量(race_results連続値+カテゴリ)から直接LGB。サブ指数は事前特徴量の1つとして併用 | 買い目整合（単勝=is_win / 複勝=is_top3を別ヘッド or 2モデル） | OOF isotonic | 混戦帯の解像度回復・未使用カラム活用・情報ボトルネック解消 | 特徴量設計・リーク管理コスト | 中〜大 |
| **B. 2段維持+較正+ヘッド分離** | 現サブ指数+メタをLGB。単勝/複勝で別モデル | 単勝モデル=is_win, 複勝=is_top3 | OOF isotonic + 温度最適化 | 既存資産流用・最小破壊 | サブ指数の圧縮限界は残る | 小〜中 |
| **C. ランキング** | 生 or サブ指数 lambdarank | rel=順位由来(roi重み検討) | rank→確率は別途較正 | 「1位の質」最適化 | 較正が非自明・回収率と乖離 | 中 |
| **D. edge特化2モデル** | (1)能力推定モデル + (2)市場乖離モデル | (1)is_top3, (2)「能力上位×人気薄で来るか」 | 各々較正 | ユーザー戦略(オッズの隙間)に直結 | 正例希少・過学習・要厳密OOS | 大 |

### 推奨ロードマップ
1. **案0を最優先で実施**（バグ・経路不整合の解消なしには以降の比較が全て無意味）。特に「本番で実際に効いているのはv9-softmaxであり、LGBアンサンブルは推論パスに居ない」事実を運用に反映/是正する。
2. **案B**で土台を作る（較正導入 + 単複ヘッド分離 + アンサンブル比を複数seed×OOS×drop1で再探索）。これだけで EVゲートの信頼性が大幅改善する見込み。
3. 余力で**案A**（1段直接学習）を A/B。混戦帯（prior_runs多）での勝率改善を主KPIに。
4. 検証作法（必須・CLAUDE準拠）: 全比較は**複数seed平均**、OOS時系列ホールドアウト、drop1(最高配当除外)、ブートストラップCI。単一seedの「当たりくじ」で採否を決めない。

### 監査で確定した「やってはいけない/直すべき」
- v11の `win_probability=y` 起因の全メトリクスを破棄。
- 確率にアンサンブルが反映されない実装の放置（sweet_spot判定が無効化されている）。
- 0.3/0.7 を地方で無検証流用。
- 単一seedでの採否判断。
- 目的変数top3のまま単勝を買う運用ミスマッチ。

---

### 200字要約
地方v10の本番実体はv9線形softmaxで、LightGBMアンサンブルは過去窓のみ・推論経路に未組込（version10へ二重書込・確率にアンサンブル非反映）。v11はwin_prob=yでEV評価がリーク。劣化原因は履歴の薄さでなく、混戦帯でサブ指数が圧縮→未較正softmaxが均す2段構成の情報損失で、降格勝ち馬の36%は市場本命。推奨は経路バグ即修正後、確率較正+単複ヘッド分離(案B)→生特徴量直接学習(案A)を複数seed・OOS・drop1・CIで比較。
