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

```bash
cd keirin
python scripts/build_race_type_board.py                       # /tmp/race_type_board.npz
python scripts/build_type_lab_picks.py --mode paper --from 2026-01-01 --to 2026-08-26
python scripts/settle_type_lab_picks.py --from 2026-01-01 --to 2026-08-26
```

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

VPS の cron へ入れる場合（既存の keirin バッチと同じホスト cron）:

```cron
# 型ラボ（検証用・入稿しない）
15 7 * * * $KEIRIN_HOME/scripts/type_lab_daily.sh >> $KEIRIN_HOME/data/logs/cron.log 2>&1
```

## 3. 見る

`/keirin/type-lab`（既存の一覧・統計とは別ページ）。
モードで **実地 / ペーパー** を切り替え、プラン別のまとめ行をクリックすると
そのプランの買い目だけに絞れる。

🔴 **ROI で採否を決めないこと**（この層は ±2.5pt に収めるのに約15.6年）。
判断指標は **件/日・表示的中（ガミ除く）・払戻中央・2倍以上の的中件/日・ガミ率**。

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
