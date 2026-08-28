# 型ラボ 検証の手順（2026-08-27）

既存商品には**一切触らない**。書き込むのは `keirin.type_lab_picks` だけで、
`picks_history` / `netkeirin_submissions` / 入稿経路は読むことすらしない
（`keirin/tests/test_type_lab.py::test_scripts_never_touch_existing_product_tables` が固定）。

## 0. マイグレーション（一度だけ）

```bash
cd backend && .venv/bin/alembic upgrade head     # 202608270930_keirin
```

## 1. ペーパー検証（過去）

vintage walk-forward の予測で組む。台が無ければ先に作る（約10分）。

予測の取り方が2つある。**どちらも学習はレースより前**（look-ahead なし）で、
違うのは再学習の刻みだけ。

| `--models` | 予測の出どころ | 使える期間 |
|---|---|---|
| `board`（既定） | 四半期 walk-forward（`/tmp/race_type_board.npz`） | 台を作った範囲（現状 2024-07〜2026-08-04） |
| `vintage` | **月次 vintage モデル**（`lgbm_wt_{eval,win}_mYYMM`） | モデルがある月すべて |

```bash
cd keirin
python scripts/build_race_type_board.py                       # /tmp/race_type_board.npz
python scripts/build_type_lab_picks.py --mode paper --from 2026-01-01 --to 2026-08-04
# 台が届かない期間は月次 vintage で埋める
python scripts/build_type_lab_picks.py --mode paper --models vintage --from 2026-08-05 --to 2026-08-26
python scripts/settle_type_lab_picks.py --from 2026-01-01 --to 2026-08-26
```

⚠️ 同じ `mode='paper'` に両方の出どころが混ざる。**比べるときは期間で切ること**
（再学習の刻みが違うので、境目をまたいで「良くなった/悪くなった」と読まない）。

⚠️ **確認窓は 2026-01 以降**（予測オッズ `odds_tf_n7` の train_end が 2025-12-31）。
2024-07〜2025-12 も入れられるが、そちらのオッズは in-sample。

## 2. 実地検証（1週間）

毎朝、当日ぶんを組む。**入稿はしない**（画面で見るだけ）。

```bash
cd keirin
python scripts/build_type_lab_picks.py --mode live --date $(date +%F)              # 7車 → mode='live'
python scripts/build_type_lab_picks.py --mode live --date $(date +%F) --n-entries 9  # 9車 → mode='live9'
```

🔴 **9車は 2026-08-28 から実投入**（`type_lab_daily.sh` が上の2本を回す）。
`--n-entries 9` を付けると **型F は決勝の `F_hit` だけ**になる（判定は
`src/type_lab.plans_for`）。全8プランのままだと ROI 69.8%/72.8% で両窓とも壁の下。
実測: `carcount_2026_08_27.md` 追記 / 実装した規則を `paper9` へ当て直すと
探索 5.51件/日・ROI 83.3% / 確認 5.97件/日・ROI 89.7%。

🔴 **9車には `data/models/odds_tf_n9.txt` が要る**（本番版は 2026-08-28 学習・
train_end 2025-12-31・`sync_models_to_vps.sh` の配布リストに入っている）。
無いと `predict_board` が例外を投げ、9車ぶんが毎朝まるごと落ちる。
日次バッチは 9車の失敗で**採点を巻き添えにしない**ようガードしてある。

🔴 **軸信頼ゲートは9車には掛からない**（閾値が7車の探索窓の分位のため。
`backend/src/services/keirin_type_lab_gate.py`）。画面でもトグルが無効になる。

翌朝、前日ぶんを採点する（**mode を見ないので 9車も同じ経路で埋まる**）。

```bash
python scripts/settle_type_lab_picks.py --date $(date -v-1d +%F)
```

VPS の cron（既存の keirin バッチと同じホスト cron）:

```cron
# 型ラボ（検証用・入稿しない）
15 7 * * *  $KEIRIN_HOME/scripts/type_lab_daily.sh  >> $KEIRIN_HOME/data/logs/cron.log 2>&1
# 🔴 当日の結果を随時反映する。日次バッチだけだと**その日の結果が翌朝まで画面に出ない**
# 🔴 間隔は `intraday_results_wt.sh`（*/15 8-23,0）に合わせる。着順・確定オッズを
#    入れているのはそちらなので、毎時1回だと**最大60分遅れて /keirin だけ先に進む**。
#    :00/:15/:30/:45 の取得が終わってから走るよう5分ずらす。
5,20,35,50 8-23,0 * * *  $KEIRIN_HOME/scripts/type_lab_settle.sh >> $KEIRIN_HOME/data/logs/cron.log 2>&1
```

⚠️ **`type_lab_settle.sh` は前日ぶんと当日ぶんの両方を流す。**
   ミッドナイトの最終レース（23:20〜23:30 発走）の着順が入るのは日付が変わった後で、
   当日ぶんだけを見ていると 00 時台の実行が翌日を指してしまい、
   その日の最後の数レースが翌朝の日次バッチまで埋まらない。

⚠️ **採点は「着順が1〜3着そろい、かつ確定オッズが引けた」行だけを埋める。**
   未確定は `settled_at` を空のまま残すので、1時間ごとに流しても二重採点は起きない。

## 3. 見る

`/keirin/type-lab`（既存の一覧・統計とは別ページ）。
プラン別のまとめ行をクリックするとそのプランの買い目だけに絞れる。

### モードの選択（**複数可**・2026-08-28）

競輪場の絞り込みと同じ操作感で、**「すべて」＋ 7車/9車 × 実地/ペーパー の4つ**を
複数選べる。API は `?mode=live,paper9`（カンマ区切り。空または `all` で全部）。

- 正規化の正本は `backend/src/api/keirin_type_lab_router.parse_modes`
  （知らない値は捨てる / 空にならない / 並びは定義順）
- SQL は3クエリとも `mode = ANY(:modes)`。**単一比較が1つでも残ると
  「複数選んでも1つしか出ない」という気づきにくい壊れ方**をするので、
  `test_every_mode_query_uses_an_array_comparison` が固定している
- 🔴 **7車と9車を混ぜると画面が注意を出す。** 型の出方が違い（9車は型F が 58% ↔
  7車 31%）、同じプランでも確定オッズの帯が 2〜3倍違うので、合計の「件/日」
  「表示的中」がどちらの話か読めなくなる
- 軸信頼ゲートのトグルは **7車を1つも選んでいないときだけ無効**になる
  （ゲートは7車にしか掛からない）

🔴 **ROI で採否を決めないこと**（この層は ±2.5pt に収めるのに約15.6年）。
判断指標は **件/日・表示的中（ガミ除く）・払戻中央・2倍以上の的中件/日・ガミ率**。

### 「プランを組み合わせた合計」（2026-08-27 追加）

プランをチェックすると、その組み合わせで売った場合の **対象レース数・的中数・
払戻・ROI** を合計で出す。API は `GET /api/keirin/type-lab/combo?plans=A_hit,B_hit,...`。

🔴 **1レースの推奨は1プラン**なので、選んだプランが**同じレースに2つ以上当たった
レースは丸ごと集計から外す**（どちらを買ったことにするか決められないため）。
除いた数は画面に「競合で除外: N レース」として必ず出る。

- 既定は **型ごとに1つずつ**（`A_hit,B_hit,C_hit,D_hit,E_hit,F_hit`）＝競合ゼロ
- `A_hit` と `A_pay` のように**同じ型の2プランを一緒に選ぶと全レースが競合**して
  対象が 0 になる。これは壊れているのではなく、**同時には売れない**という意味
- 的中・払戻・ROI は**採点済みのレースだけ**で計算する
  （未採点を分母に入れると当日の朝ほど ROI が 0 に近く見える）

集計の本体は `backend/src/api/keirin_type_lab_router.combine_plans()`（DB に依存しない
純関数）。競合除外の規則は `backend/tests/test_keirin_type_lab_router.py` が固定している。

### 「軸信頼ゲート」（2026-08-27 追加・**検証中で採用ではない**）

合計表のスイッチ。各プランの中で**軸信頼（上位2車の3着内率の合計）が下位1/5（2割）**の
レースを外す。`type_lab_picks.axis_sum` と比べるだけなので**買い目は作り直さない**
（実地検証の最中でも既存の行に後から当てられる）。

- 閾値の正本: `backend/src/services/keirin_type_lab_gate.py`（**プランごとに違う値**）
- API: `GET /api/keirin/type-lab/combo?...&axis_gate=true`
- ペーパー実測: 51.1 → 29.5件/日・表示的中 24.5 → 27.8%・ROI 80.1 → 85.0%
  （全体との差 +4.9pt CI[+0.1, +9.9]・**無作為に同数を落とす対照20本に20/20で勝ち**）

🔴 **絶対閾値では効かない**（本番7Cの 1.44 を含め 1.20〜1.50 のどこも 0 を跨ぐ）。
   効くのは「各プランの中で相対的に下を外す」形だけ。
🔴 **確認窓を判断に使ってしまっている**（一度きりを消費）。向きが両窓で一致するのは
   6プラン中3つ。**実地で確かめるために置いている。**

詳細: `ev_axis_rank_2026_08_27.md`

### 「型分けの答え合わせ」（2026-08-27 追加）

事前の分割（型・相手の開き）が**実際の決着と合っていたか**をマトリクスで出す。
API は `GET /api/keirin/type-lab/outcome`、計算の正本は
`backend/src/services/keirin_type_lab_outcome.py`（DB にも FastAPI にも依存しない純関数）。

| 表 | 行 | 列 | 何が言えるか |
|---|---|---|---|
| ① | 型 A〜F | 決着クラス5種 | 軸の堅さの分割が的中率を分けているか |
| ② | 相手の開き（`gap` 3分位） | 決着クラス5種 | 開きが3着の出どころを分けているか |
| ③ | 型 A〜F | 三連単の確定オッズ帯 | 荒れ度の分割が配当を分けているか |
| ④ | プラン | 決着クラス5種（**セルは的中率**） | どの決着で取れて、どこで落としているか |

**決着クラス**は指数（3着内率）順位で「1〜3着に入った3車がどこから来たか」を5つに分ける:
`順当`（軸2車＋指数3〜4位）/ `軸2+穴`（軸2車＋指数5〜7位）/ `片軸+中位` /
`片軸+穴` / `軸崩壊`（指数1位も2位も3着外）。

🔴 **指数の並び（`p3_order`）は行を作った時点でしか残せない。**
後から `wt_entries` を引き直すと、モデルの再学習ぶんだけ当時と違う並びになる
（paper は vintage・live は当日の本番モデル）。**並びの無い行は分類しない**——
分類できなかった件数は画面に必ず出る。

🔴 **分割が当たっている ＝ 儲かる ではない。** 型は edge を作らず、決めるのは
「同じ買い方でどの帯へ落ちるか」と「どのレースを拾えるか」だけ（SUMMARY 2.6）。

#### 古い行を埋める（2026-08-27 以前に作られた行）

```bash
# 決着の三連単オッズ（軽い・全モード）
python scripts/backfill_type_lab_outcome.py --odds --from 2026-01-01 --to 2026-08-27

# 指数の並び — paper の四半期 walk-forward ぶん（/tmp/race_type_board.npz から）
python scripts/backfill_type_lab_outcome.py --order-from-board --from 2026-01-01 --to 2026-08-04

# 指数の並び — モデルを回して復元（paper は月次 vintage / live は本番モデル）
python scripts/backfill_type_lab_outcome.py --order-from-models --mode paper \
    --from 2026-08-05 --to 2026-08-26
python scripts/backfill_type_lab_outcome.py --order-from-models --mode live \
    --from 2026-08-27 --to 2026-08-27
```

🔴🔴 **見るのは「行が合っているか」ではなく「ソースが正しいか」。**
突き合わせられるのは `axis1`/`axis2` ＝ **並びの先頭2つだけ**で、3位以下は照合できない。
そして**違うソース**だと「先頭2つが合っていれば3位以下も合っている」は成り立たない:

    2025-07-15 実測 — 違うソースで復元した 47 行のうち
      完全一致 24 / **先頭2つだけ一致 23** / 不一致 0

幸い、ソースが正しいかは**食い違い率で判る**（実測は桁で分かれる）:

| 復元ソース | 対象 | 先頭2つの食い違い率 |
|---|---|--:|
| 板 npz | paper 2026-01〜08-04 | **0.00%** ✅ |
| 月次 vintage | paper 2026-08-05〜08-26 | **0.00%** ✅ |
| 月次 vintage | paper 2025-01 | **0.07%**（2/2,797）✅ |
| 板 npz | paper 2025 全期間 | **34%** 🔴 違うソース |

→ `AXIS_MISMATCH_LIMIT_PCT`（既定 1%）を超えたら**その範囲は1行も書かない**。
下回れば食い違った行だけ飛ばして書く。

⚠️ **`--order-from-models` は vintage 窓ごとにまとめて予測する**（1日ずつではない）。
特徴量の構築は期間の長さにほとんど比例しない（履歴の読み込みが支配的）ため、
1日ずつ回すと 2025年ぶんで9時間かかる。

### 現況（2026-08-28 時点・充填済み）

| mode | 期間 | 行 | `p3_order` | 由来 |
|---|---|--:|--:|---|
| `paper` | 2025 全期間 | 26,189 | **26,169** | 月次 vintage（食い違い 0.00〜0.24%） |
| `paper` | 2026-01-01〜08-04 | 15,978 | **15,978** | `/tmp/race_type_board.npz`（食い違い 0.00%） |
| `paper` | 2026-08-05〜08-26 | 2,121 | **2,121** | 月次 vintage（食い違い 0.00%） |
| `live` | 2026-08-27〜 | — | **自動** | 生成時に入る（バックフィルは不要） |
| `paper9` | 2025〜2026 | 5,105 | **0** 🔴 | **9車。未対応**（下記） |

⚠️ **2025年ぶんを板 npz で埋めてはいけない**（食い違い 34% ＝ 別ソース）。
月次 vintage（`--order-from-models --mode paper`）で埋めること。

🟢 **`paper9`（9車）も同じ経路で埋まる**（2026-08-28 対応）。
paper9 は `build_type_lab_picks --n-entries 9` が**月次 vintage** で作っているので、
`--order-from-models --mode paper9` で復元できる（実測 2025-01 は 202/202 一致・0.00%）。
板 npz は7車のみなので `--order-from-board` では埋まらない。

```bash
python scripts/backfill_type_lab_outcome.py --order-from-models --mode paper9 \
    --from 2025-01-01 --to 2026-08-31
python scripts/backfill_type_lab_outcome.py --odds --mode paper9 \
    --from 2025-01-01 --to 2026-08-31
```

画面は「9車ペーパー（検証）」で選べる（既定は実地のまま）。
🔴 **7車と混ぜて読まないこと。** 型の出方が違う（9車は F 大混戦が 58% ↔ 7車 31%）。

詳細と実測: `outcome_matrix_2026_08_27.md`

## 4. プランを変えるとき

`keirin/src/type_lab.py` の `PLANS` を編集する。`rule_version()` が自動で変わるので
`type_lab_picks.rule_version` で新旧の世代が分かれる。
🔴 **同じ期間を作り直すと UPSERT で上書きされる**（`(race_key, plan_key, mode)` が一意）。
世代を比べたいなら先に旧世代を別テーブルへ退避するか、期間を分けること。

## 5. 🟢 全面置き換え（2026-08-28 実施）

**決定と手順は `GO_LIVE_2026_08_28.md`。** 2026-08-29 07:20 の朝バッチから
既存ランクを止めて型ラボの6プラン（A〜E は `_hit` / F は `_pay`）へ全面移行した。

- 入稿は専用スクリプト `scripts/netkeirin_submit_type_lab.py`
  （**既存の `netkeirin_submit_wt.py` は1行も変えていない**）
- 切り替えもロールバックも `netkeirin_settings.enabled` の SQL だけで済む
- 1レース1商品は**型が排他であること**で構造的に守られる（優先順位の設計が要らない）
- 看板レースは軸信頼ゲートを素通しする（「看板は必ず出す」を優先）

§5 にあった旧「決めること」4項目はすべて解消済み:

| 旧課題 | 決着 |
|---|---|
| 1レース1商品の割り当て | `sell_plans_for` が型ごとに1プランを返す＝競合なし |
| 三連単経路の明示ゲート | 平均想定払戻 20,000円 を三連単にも掛けた（`_gate_reason`） |
| 入稿の経路（12点は1点1行が要る） | `BET_KIND_TRIFECTA_FORMATION` を1点=1行で送る |
| 売上（pt/R）の測り直し | **未了**。移行後1週間の日次監視で測る（`GO_LIVE` §10） |
