# 探索用 vintage 予測オッズ（odds_tf, train_end 2024-12-31）

事前登録: `README.md`「事前登録 — Phase 4」。2025 を探索窓に使うための、本番 `odds_tf_n7/n9`（train_end 2025-12-31）の学習終端違い版。

## 手順
1. `scripts/train_odds_prediction_tf.py` は **無改造で使用**。出力先は環境変数 `KEIRIN_ODDS_TF_MODEL_DIR`（`src/odds_prediction_tf.py` が元から対応）で `data/exp_bet_review/models/` へ。引数は `--train-end 2024-12-31 --n-car {7,9}`（他は既定: rounds 600, max-races 12000, LightGBM 同一パラメータ）。
2. 7車は `.venv`、9車は `../backend/.venv`（9車の `wf_preds9_*.pkl` が numpy2 で pickle 済みで keirin venv では読めないため）。numpy/lightgbm 版が違う点のみ差（学習アルゴリズム・パラメータは同一）。
3. 予測: `scripts/exp_bet_review/odds_tf_vintage_predict.py`（本番モデルは読むだけ。前後で sha1 が不変なことを確認済み）。本番と同じ `build_race_features` → `10**predict` → クリップ(>=1) → レース内で Σ1/o=target_sum に再スケール（`predict_board` と同じ）。target_sum は vintage の学習窓の値（7車 1.3372 / 9車 1.3332）。

## 本番との構成差
| 項目 | 本番 | vintage |
|---|---|---|
| 特徴量 | 63個 | 同一（メタ照合済み・assert） |
| ハイパーパラメータ | 同一 | 同一 |
| 学習レース（7車） | 7145R（≤2025-12-31） | **2790R**（≤2024-12-31） |
| 学習レース（9車） | 3536R | **1736R** |
| target_sum 7/9 | 1.3364 / 1.3332 | 1.3372 / 1.3332 |
| 保守倍率 p25（7/9） | メタ参照 | 0.8762 / 0.8852 |
差は学習データ量（約 4 割〜半分）のみ。walk-forward p3/pw が 2024-07 開始（9車は 2024-01）で、学習窓が 2024 年内に限られるため。

## 🔴 入力 p3/pw
本番学習も `data/exp_cache/axis_detail_7car.pkl`（7車）/ `wf_preds9_*.pkl`（9車）の **walk-forward（vintage）予測**を使っている（in-sample の full-refit 値ではない）。`race_type_board.npz` の `P3/PW` とは 5000 レースで最大差 3e-8（float32 丸め）＝**同一**。揃えられている。
推論時の 2025 各レースの入力も同じ walk-forward 値。→ 本 vintage の 2025 予測オッズに、p3/pw 経由の look-ahead は無い。

⚠️ 本番「in-sample」の実態: 本番は 7車 48,541R から 12,000R を等間隔に間引いて学習。2025 の7車 23,056R のうち学習に入ったのは 5,699R（約25%）。したがって「本番=in-sample」比較は 2025 の約 1/4 が学習済み・残りは未使用で、差は小さく出る。

## 品質（2025 全レース、最終オッズ（充足率85%以上のレース）に対するレース内の log10 オッズ、レース平均）
| 車数 | 対象R | モデル | 対数相関 | 順位相関 | logMAE |
|---|---|---|---|---|---|
| 7 | 22,103 | vintage | 0.9400 | 0.9294 | 0.1729 |
| 7 | 〃 | 本番(in-sample) | 0.9457 | 0.9359 | 0.1779 |
| 9 | 1,800 | vintage | 0.9290 | 0.9192 | 0.1785 |
| 9 | 〃 | 本番(in-sample) | 0.9438 | 0.9359 | 0.1615 |
- 7車: 相関は vintage が −0.006 だが logMAE は vintage の方が良い（本番の目標総和・分位の差）。実質同等。
- 9車: vintage が −0.015（相関）/ logMAE +0.017 劣る。9車は学習量が半分のため。
- 学習スクリプト自身の test（2025+2026 全体、整合板）: 7車 logMAE 0.1746・±2倍以内 83.5%、9車 0.1817・81.9%（本番 test は 2026 のみで 0.1749/0.1847 だが窓が違うので比較不可）。
- 目視: 20250122_74_03（7車）210点、最小 3-2-4 が 3.47倍、最大 6-7-5 が 5953倍、Σ1/o = 1.3372（=target_sum）。

## 所在（git 管理外 `keirin/data/exp_bet_review/`）
- モデル: `models/odds_tf_n7.txt` `odds_tf_n9.txt` `odds_tf_meta.json`
- 予測: `odds_tf_vintage2024_2025.npz`（race_key U14, n_car, combination "1-2-3" 着順つき, pred_odds）。7車 23,056R + 9車 2,175R = 25,231R × 210/504点。pandas/numpy 版依存を避け npz。`np.load` → DataFrame 化して使う。
- 品質: `odds_tf_vintage_quality.json`、ログ `train_odds_tf_vintage2024*.log` `odds_tf_vintage_predict.log`
- スクリプト: `scripts/exp_bet_review/odds_tf_vintage_predict.py`

## 注意
- 全 2025 の walk-forward 入力があるレースは全て出力（最終板が無いレースも含む）。`race_type_board.npz` の 2025 7車 17,756R のうち 17,708R が覆われる（残り 48R は axis_detail_7car.pkl に無い（原因は未調査））。
- 予測は 7車 23,056R は npz より多い（npz は型付け可能なレースのみ）。
- 本番 `data/models/odds_tf_*` は未変更（sha1 一致）。
