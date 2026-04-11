# 再帰的改善Agent システム 設計書

## 1. 目的

競馬指数の回収率継続向上を自動化する。特に以下を重点課題とする：

- **穴馬的中率の向上**: 単勝オッズ10倍以上 × 3着以内の馬を事前検出
- **ROI改善**: 指数上位馬は人気になりやすく低オッズ。指数下位の穴馬を複勝・単勝で拾うことでROI底上げ

改善ループはユーザーが手動でトリガーし、バックテスト→分析→提案まで自動実行。採用/却下はユーザーが判断する。

---

## 2. システム全体図

```
$ python scripts/run_improvement_cycle.py --train 20230101-20241231 --test 20250101-20251231

┌─────────────────────────────────────────────────────────────────┐
│  Orchestrator (run_improvement_cycle.py)                        │
│                                                                 │
│  Step 1 ──► Backtester        現行ベースライン計測              │
│  Step 2 ──► Analyst Agent     穴馬パターン分析 + 候補生成       │
│  Step 3 ──► Feature Engineer  交互作用項込みウェイト最適化      │
│  Step 4 ──► Backtester A/B    現行 vs 新ウェイト 比較           │
│  Step 5 ──► レポート出力      Markdown + improvement_log.json  │
│                                                                 │
│  ユーザー判断 (y/n) → INDEX_WEIGHTS 更新差分を表示             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 各 Agent の責務

### 3.1 Orchestrator（run_improvement_cycle.py）

**責務**: 改善サイクル全体の管理・履歴管理・レポート生成

- 各 Agent を逐次呼び出す
- `improvement_log.json` に全サイクルの記録を永続化
- 停止条件の判定:
  - 検証期間の穴馬ROIが訓練期間比 -10% 以上 → 過学習フラグ（自動リジェクト候補）
  - 全体3着内率が現行比 -5% 以上悪化 → 警告表示
  - 連続 3 サイクル改善なし → 探索行き詰まりを通知
- 採用時: `src/utils/constants.py` の `INDEX_WEIGHTS` 更新差分を表示（ユーザーが手動適用）

**CLI インターフェース**:
```bash
python scripts/run_improvement_cycle.py \
  --train 20230101-20241231 \
  --test 20250101-20251231 \
  [--objective upside_place_roi]  # デフォルト
```

**改善履歴フォーマット** (`improvement_log.json`):
```json
{
  "cycles": [
    {
      "cycle_id": 1,
      "timestamp": "2026-04-11T10:00:00",
      "train_period": "20230101-20241231",
      "test_period": "20250101-20251231",
      "baseline": {
        "place_rate_pct": 42.1,
        "win_roi_pct": 82.3,
        "upside_hit_rate": 0.123,
        "upside_win_roi_pct": 145.2,
        "upside_place_roi_pct": 98.7
      },
      "candidate_interactions": ["speed_index*rebound_index", "course_aptitude*pace_index"],
      "new_weights": { "speed_index": 0.13, ... },
      "result": {
        "place_rate_pct": 44.5,
        "upside_hit_rate": 0.142,
        "upside_place_roi_pct": 112.3,
        "overfit_flag": false
      },
      "decision": "adopted",  // "adopted" | "rejected" | "pending"
      "note": ""
    }
  ]
}
```

---

### 3.2 Analyst Agent（analyst_agent.py）

**責務**: 穴馬パターン分析 + 交互作用項候補生成

既存 `upside_detection.py` と `ev_analysis.py` の分析関数を統合・発展させる。

**穴馬の定義**（本システムにおける確定定義）:
```
odds_upside_flag = (win_odds >= 10.0) AND (finish_position <= 3)
```
※ `upside_detection.py` の `upside_flag`（指数4位以降×3着内）は参考指標として併用

**分析内容**:

1. **個別指数リフト分析** (`upside_detection.py::analyze_upside_patterns` を継承)
   - 穴馬的中時 vs 外れ時で各指数の「突出率」「平均値」を比較
   - リフト上位指数 = 穴馬を引き当てやすいシグナル

2. **交互作用項候補スコアリング**
   - C(12, 2) = 66 通りの 2 項積を生成
   - 候補スコア = `穴馬時相関 × リフト` で上位 15 個を選定
   - 出力: `interaction_candidates.json`

3. **悪条件フィルター候補** (`ev_analysis.py` の悪条件検出を統合)
   - 全体ROIが低い条件軸（馬場/距離/グレード/競馬場）を特定
   - 穴馬ROIとの交差分析（穴馬には有利な条件が異なる場合あり）

4. **出力**:
   ```json
   {
     "upside_profile": { ... },
     "top_interactions": [
       {"feature": "speed_index*rebound_index", "score": 0.087, "upside_corr": 0.12, "lift": 0.73},
       ...
     ],
     "bad_conditions": { ... }
   }
   ```

**主要関数**:
```python
def run_analysis(df: pd.DataFrame) -> dict  # 全分析を実行し結果dictを返す
def score_interactions(df: pd.DataFrame, top_n: int = 15) -> list[dict]  # 交互作用項候補生成
def analyze_upside_by_condition(df: pd.DataFrame) -> dict  # 悪条件フィルター分析
```

---

### 3.3 Feature Engineer（feature_engineer.py）

**責務**: 交互作用項込みウェイト最適化（weight_optimizer.py を発展）

**入力**:
- 訓練データ（DB から取得した指数 + 結果 + オッズ）
- Analyst Agent が生成した `top_interactions` リスト

**拡張指数ベクトル**:
```
元の12指数 + 交互作用項（≤15個）= 最大27次元
```

例:
```python
df["speed_index*rebound_index"] = df["speed_index"] * df["rebound_index"] / 50.0
# ÷50 で各指数のスケール(0-100)をそろえる
```

**最適化目標** (`--objective` で切り替え可):

| objective | 説明 |
|-----------|------|
| `upside_place_roi` | 穴馬複勝ROI最大化（デフォルト） |
| `upside_win_roi` | 穴馬単勝ROI最大化 |
| `place_rate` | 指数1位の3着内率最大化 |
| `roi` | 指数1位の単勝ROI最大化（既存） |

**最適化手法**:
- Nelder-Mead + Softmax 変換（全非負・合計1保証）
- L2 正則化: 現ウェイトからの乖離にペナルティ（`λ = 0.5`）
- 5-Fold CV で過学習ガード
- 交互作用項ウェイトは元指数ウェイトとは**別のバジェット** (最大 `0.20`) から割り当て

**過学習検出**:
```
overfit_flag = (test_roi < train_roi * 0.90)
```

**出力**: 新ウェイト候補 dict（元指数12個 + 交互作用項N個）

---

### 3.4 Backtester（backtest.py 拡張）

**責務**: 現行 vs 新ウェイトの A/B 比較評価

既存の `backtest.py` に穴馬ROI集計を追加する。

**追加する評価指標**:

| 指標 | 定義 |
|------|------|
| `upside_hit_rate` | 全レースで `upside_score` 上位3頭に穴馬が1頭以上含まれる率 |
| `upside_win_roi_pct` | 穴馬候補（upside_rank ≤ 3）全頭単勝購入時の ROI |
| `upside_place_roi_pct` | 穴馬候補（upside_rank ≤ 3）全頭複勝購入時の ROI |
| `composite_place_rate` | 従来の指数1位3着内率（既存） |
| `composite_win_roi_pct` | 従来の単勝ROI（既存） |

**穴馬候補の定義**（バックテスト評価時）:
```
upside_rank ≤ 3 (upside_scoreの上位3頭を購入対象とする)
```

---

## 4. データフロー

```
DB: keiba.calculated_indices + keiba.race_results + keiba.odds_history
                    │
              load_data()  ← upside_detection.py の実装を共通利用
              (12指数 + finish_position + win_odds + place_odds)
                    │
            ┌───────┴───────────────────────────────┐
            │                                       │
      Analyst Agent                          Feature Engineer
      ・穴馬ケース抽出                        ・交互作用項を追加
      ・個別指数リフト分析                    ・Nelder-Mead最適化
      ・交互作用項候補スコアリング             ・過学習検出
            │                                       │
            └────────────── interaction_candidates ─┘
                                    │
                             Backtester A/B
                          ・現行 vs 新ウェイト比較
                          ・穴馬ROI + 全体指標
                                    │
                             Orchestrator
                          ・Markdownレポート出力
                          ・improvement_log.json 更新
                          ・ユーザー採用/却下入力
```

---

## 5. ファイル構成

```
backend/
  scripts/
    run_improvement_cycle.py   # Orchestrator（新規）
    analyst_agent.py           # Analyst Agent（新規）
    feature_engineer.py        # Feature Engineer（新規）
    improvement_log.json       # 改善履歴（自動生成）
    upside_detection.py        # 既存維持（関数をimport元として活用）
    ev_analysis.py             # 既存維持（EVロジックをimport元として活用）
    weight_optimizer.py        # 既存維持（Nelder-Mead基盤として活用）
    backtest.py                # 既存（穴馬ROI集計を追加）
  docs/
    improvement_agent_design.md  # 本ドキュメント
```

---

## 6. 実装順序

| 優先度 | ファイル | 概要 |
|--------|---------|------|
| 1 | `analyst_agent.py` | 穴馬パターン分析 + 交互作用項候補JSON生成 |
| 2 | `feature_engineer.py` | 交互作用項込みウェイト最適化 |
| 3 | `backtest.py` 拡張 | 穴馬ROI集計（upside_hit_rate / upside_*_roi_pct）追加 |
| 4 | `run_improvement_cycle.py` | Orchestrator（ループ管理・レポート生成） |

---

## 7. 停止条件と安全弁

| 条件 | アクション |
|------|-----------|
| 検証期間の穴馬ROI < 訓練期間比 90% | `overfit_flag=True`、リジェクト推奨を表示 |
| 全体3着内率が現行比 -5% 以上悪化 | 警告表示（ユーザー判断） |
| 連続3サイクル改善なし | 「探索行き詰まり」通知 |
| 交互作用項ウェイト合計 > 0.20 | バジェット上限でクリップ（過剰複雑化防止） |

---

## 8. 使用方法

```bash
# 基本実行（訓練2023-2024、検証2025）
cd backend
uv run python scripts/run_improvement_cycle.py \
  --train 20230101-20241231 \
  --test 20250101-20251231

# 穴馬単勝ROIを主目標に変更
uv run python scripts/run_improvement_cycle.py \
  --train 20230101-20241231 \
  --test 20250101-20251231 \
  --objective upside_win_roi

# 分析のみ（最適化なし）
uv run python scripts/analyst_agent.py \
  --start 20240101 --end 20261231

# ウェイト最適化のみ（分析済み候補を再利用）
uv run python scripts/feature_engineer.py \
  --train 20230101-20241231 \
  --test 20250101-20251231 \
  --interactions scripts/interaction_candidates.json
```
