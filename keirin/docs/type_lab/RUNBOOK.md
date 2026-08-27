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
python scripts/build_type_lab_picks.py --mode live --date $(date +%F)
```

翌朝、前日ぶんを採点する。

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
モードで **実地 / ペーパー** を切り替え、プラン別のまとめ行をクリックすると
そのプランの買い目だけに絞れる。

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

合計表のスイッチ。各プランの中で**軸信頼（上位2車の3着内率の合計）が下位4割**の
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

## 4. プランを変えるとき

`keirin/src/type_lab.py` の `PLANS` を編集する。`rule_version()` が自動で変わるので
`type_lab_picks.rule_version` で新旧の世代が分かれる。
🔴 **同じ期間を作り直すと UPSERT で上書きされる**（`(race_key, plan_key, mode)` が一意）。
世代を比べたいなら先に旧世代を別テーブルへ退避するか、期間を分けること。

## 5. 全面置き換えへ進むときに決めること

1. 1レース1商品の割り当て（型ラボは1レースに最大2プラン出す。既存ランクとの重複も要整理）
2. 三連単経路の明示ゲート（`Σ(1/予測オッズ) <= 0.6` 等。既存の平均払戻ゲートは三連単に効かない）
3. 入稿の経路（型C の12点・型F の12点は 1点1行が要る。`formation_bet_7t1` では表現できない）
4. 売上（pt/R）の測り直し。想定払戻3〜4万円の三連単には前例が無い
