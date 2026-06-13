# T06: 資金配分・買い目構築モジュール（購入効率化）

## 目的
「何をいくら買うか」を機械的に決める層。同じ的中率・配当でも資金配分でROIの分散と破産確率が大きく変わる。
分数Kelly・点数制約・レース内予算の3点を実装し、T09 統合検証とT10本番化の共通部品にする。

## 機能要件
### 1. 分数Kelly 配分（`backend/src/betting/allocation.py`）
- 入力: 候補ベット列 `(bet_type, combination, est_prob, odds)` + バンクロール + kelly_fraction（既定 0.25）
- 同一レース内の排反/重複関係を考慮した同時Kelly（厳密解が重い場合は「単純Kelly→レース内予算で按分」の近似でよい。近似誤差をdocstringに明記）
- 出力は100円単位に丸め。最小購入額未満は切り捨て
- est_prob が信頼できない前提（過去検証: モデルEVは過大評価しがち）→ **確率を shrinkage するオプション**（p' = α·p + (1-α)·p_market）を必ず付ける

### 2. 制約レイヤ
- ルート `.env` の既存制約と整合: BET_MAX_PER_RACE / BET_MAX_PER_DAY / BET_MAX_PER_TICKET / BET_MIN_EXPECTED_VALUE / BET_MAX_CONSECUTIVE_LOSSES（値の読み込みは `backend/src/core/config.py` 相当の既存設定経路を調べて再利用）
- 点数制約: 券種別最大点数（例: 三連単 ≤ 12点/R）。超える場合は EV 降順で切る

### 3. 買い目構築ヘルパー
- 軸流し（1軸/2軸）・BOX・フォーメーションを (bet_type, 軸馬, 相手馬列) から組合せ列に展開する純関数群
- T03 の combo 確率列挙と組み合わせて「確率上位N点フォーメーション自動構成」を提供

### 4. シミュレーション
- `backend/scripts/simulate_bankroll.py` — T05 の SettleResult を入力に、配分方式（均等/Kelly 0.1/0.25/0.5）別の資金曲線・最大ドローダウン・破産確率（モンテカルロ1万系列）を比較

## スコープ
- `backend/src/betting/allocation.py` / `backend/src/betting/ticket_builder.py`
- `backend/scripts/simulate_bankroll.py`
- `backend/tests/test_allocation.py` / `backend/tests/test_ticket_builder.py`

## 注意
- DBアクセス不要（純粋ロジック）。T05 と同パッケージなので**他タスクのファイルを上書きしない**
- IPAT連携・実購入は完全にスコープ外
- Kelly はログ最適であり短期分散が大きい。既定 fraction=0.25 の根拠を docstring に記載

## DoD
- `pytest tests/test_allocation.py tests/test_ticket_builder.py -q` 通過 / ruff 通過
- 既知の手計算例（2択Kelly等）と一致するテストケースあり
- フォーメーション展開が JRA 公式の点数公式と一致（例: 3連単フォーメーション 2×3×4 の点数）

## 完了報告に含めること
API シグネチャ一覧 / Kelly 近似の妥当性メモ / .env 制約との接続点 / T05・T03 との結合方法（コード例）
