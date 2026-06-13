# T03: 条件付き着順確率モデル（2着・3着構造の専用モデル化）

## 目的
エキゾチック馬券の確率エンジンを Harville（1着確率の鏡像）から改善する。
Harville は「2着争いも1着能力順に決まる」と仮定するが、実際は脚質・展開で2-3着の構造は異なる
（v26取りこぼし分析: 市場追従の能力推定では妙味帯を捕れない → 2-3着の独自構造が直交情報になりうる）。

## アプローチ（3者比較）
1. **Harville**（ベースライン・実装済み）: `backend/src/indices/composite.py:803-860`
2. **割引指数モデル（Henery/Stern系）**: 2段目以降を p_i^λ で再正規化。λ∈(0,1] を券種別にデータでフィット（λ=1がHarville）。実装が軽く効果が出やすい既知手法
3. **2-3着専用 LightGBM**: 目的変数=「2着以内」「3着以内」をレース内ランキング学習し、1着確率（v26）と組み合わせて条件付き確率を構成。特徴は v26 と同じソース＋脚質・上がり系を優先

## 入力データ（読み取り専用）
- `keiba.calculated_indices` の win_probability（v26）— **最初にカバレッジ監査**（期間×レースの充足率）
- `keiba.race_results` / `keiba.race_entries` の着順・脚質関連
- 確定単勝オッズ（市場確率ベースの Harville とモデル確率ベースの Harville 両方を比較対象に）

## 評価（どれを採用するかの判定基準）
- test 期間の**実現組合せに対する log-loss**: 馬連的中組合せ・三連複的中組合せ・三連単的中組合せの予測確率の対数尤度を3手法で比較
- 較正: 予測組合せ確率を decile に分け実現率と比較（券種別）
- 判定は train/test/fresh 3分割 + LGB は 5seed×deterministic 平均（PLAN.md 検証標準）

## スコープ
- `backend/src/betting/finish_order.py` — 統一インターフェース:
  `combo_probability(win_probs: dict[int,float], combo: tuple[int,...], bet_type: str, method: str) -> float`
  および全組合せ一括 `enumerate_combo_probs(...)`（三連単18頭=4896点が1レース1秒以内で列挙できる実装）
- `backend/scripts/fit_finish_order_lambda.py` — λフィット（結果は `backend/models/finish_order_lambda.json`）
- `backend/scripts/train_finish_order_lgb.py` — 2-3着専用LGB（モデルは `backend/models/finish_order_lgb.txt`）
- `backend/scripts/compare_finish_order_models.py` — 3者比較レポート
- `backend/tests/test_finish_order.py`（確率の和=1チェック・既知小例の手計算一致）

## 注意
- composite.py の Harville を**変更しない**（本番経路に触らない）。ロジックは finish_order.py 側に独立実装し、小例で composite.py と一致することをテストで確認
- 大量クエリは期間を区切る（一括3年ロードはチャンク分割）

## DoD
- `pytest tests/test_finish_order.py -q` 通過（和=1・対称性・Harville一致）
- `compare_finish_order_models.py --start 20250101 --end 20250331` スモーク完走で3手法の log-loss 表が出る
- ruff 通過

## 完了報告に含めること
3手法の log-loss / 較正比較（券種別）/ 採用推奨と理由 / λ推定値 / win_probability カバレッジ監査結果 / フル期間実行コマンド
