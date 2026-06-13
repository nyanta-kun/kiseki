# T02: 払戻ベース組合せオッズ近似モデル（歴史エキゾチックEVバックテストの実現）

## 目的
発走前エキゾチックオッズの歴史データは存在しない（取得は T01 で開始）。
そこで `keiba.race_payouts`（2020〜2026・全8券種・約2.2万レース）の**的中組合せの確定オッズ（=払戻/100）**と
Harville 確率から、任意の組合せの「市場オッズ近似値」を出すモデルを作り、歴史バックテスト（T09）でEV計算に使えるようにする。

## アプローチ
1. 理論ベースライン: 組合せ確率 p（Harville: 確定単勝オッズ→単勝確率→組合せ確率）に対し、市場オッズ ≈ (1 - takeout) / p_market。
   仮説: log(確定オッズ) ≈ a + b·log(1/p_harville)。券種別 takeout は JRA 公表値（単勝20%/複勝20%/馬連22.5%/ワイド22.5%/三連複25%/三連単27.5%）
2. 的中組合せ 2.2万件 × 券種で回帰し、残差を分析（人気サイド/穴サイドで系統的歪み = favorite-longshot bias を券種別に推定）
3. **選択バイアスに注意**: 的中組合せは「人気寄りに偏ったサンプル」。ワイド（3組合せ/R）・複勝（3頭/R）の多観測券種でバイアスの形を推定し、三連系に外挿する妥当性を検証すること
4. 検証: hold-out レースで log-odds の MAE / 較正曲線。さらに 2026-03-28 以降は odds_history の win/place 実発走前オッズがあるので、win/place で「近似 vs 実オッズ」の一致度を直接測る

## 入力データ（読み取り専用・本番DBへの書き込み禁止）
- `keiba.race_payouts`（bet_type: win/place/quinella/wide/trio/trifecta, payout=100円あたり, combination）
- 確定単勝オッズ: `keiba.race_entries` の odds 系カラム（**最初にカバレッジを監査**。欠損が多い場合は race_payouts の win 払戻から勝ち馬のみ補完 + odds_history で直近分を補う。欠損構造を完了報告に明記）
- Harville 実装の参考: `backend/src/indices/composite.py:803-860 _harville_place_probs()`（コピーせず共通化できるなら関数を切り出して再利用）

## スコープ（新規ファイルのみ・既存コード変更は composite.py の関数切り出しまで）
- `backend/src/betting/odds_model.py` — 近似モデル本体（券種・組合せ・単勝確率ベクトル → 近似オッズ）。学習済みパラメータは JSON で `backend/models/odds_approx_v1.json` に保存
- `backend/scripts/fit_odds_approximation.py` — 学習スクリプト（--start/--end 指定可・デフォルト 2023-01-01〜）
- `backend/scripts/validate_odds_approximation.py` — 検証スクリプト（券種別 MAE・較正レポートを stdout に出力）
- `backend/tests/test_odds_model.py`

## 検証標準
- train: 2023-01〜2025-06 / test: 2025-07〜2026-03 / fresh: 2026-04〜（PLAN.md の3分割に準拠）
- 受け入れ基準: test で log10(オッズ) MAE ≤ 0.25（約1.8倍以内）を目安。**達しない場合「近似精度不足で三連系EVバックテストは不可」という結論も正当な成果**として報告すること（無理に基準を緩めない）

## DoD
- `.venv/bin/python scripts/fit_odds_approximation.py --start 20250101 --end 20250331` がスモーク完走（3ヶ月窓・本番DB負荷配慮）
- `.venv/bin/python -m pytest tests/test_odds_model.py -q` 通過
- 検証スクリプトが券種別 MAE と較正テーブルを出力

## 完了報告に含めること
券種別の近似精度（MAE・バイアス方向）/ 単勝オッズ欠損の監査結果 / favorite-longshot bias の推定形 / 三連系外挿の妥当性判断 / EVバックテストに使えるか可否判定
