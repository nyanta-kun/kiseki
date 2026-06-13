# T05: 統一バックテストハーネス（6券種共通・検証標準の強制装置）

## 目的
単勝・複勝・馬連・ワイド・三連複・三連単を**同一APIで決済・集計**できるバックテスト基盤。
これまで券種・戦略ごとに散在したスクリプト（backtest_*.py 13本）の決済ロジックを統一し、
T02〜T04・T06 の成果と Wave 2 の統合検証（T09）が全てこの上で動くようにする。

## 設計
### コア API（`backend/src/betting/backtest.py`）
```python
@dataclass
class Bet:
    race_id: int
    bet_type: str          # win/place/quinella/wide/trio/trifecta
    combination: str       # race_payouts の combination 表記に正規化（例 "01" "01-02" "01-02-03"）
    stake: int             # 円（100円単位）
    tag: str = ""          # 戦略名（集計キー）

def settle(bets: list[Bet], conn) -> SettleResult: ...
```
- 決済は `keiba.race_payouts` 照合のみ（オッズ近似に依存しない確定値）
- combination の正規化関数を提供（順不同券種はソート・三連単は着順保持。**実DBの表記を最初に監査して合わせる**。馬単/三連単の区切り文字が違う可能性に注意）

### 集計（`SettleResult`）
- 戦略タグ別 × 券種別: n_bets / n_hits / hit_rate / 総投資 / 総払戻 / ROI
- **ブートストラップ95%CI**（レース単位リサンプリング・1万回）
- 期間分割レポート: 任意の分割日リストで train/test/fresh 別集計
- 月次推移テーブル（ドローダウン把握用）

### スクリプト
- `backend/scripts/roi100_backtest.py` — CLI。戦略を JSON/Python プラグインで受け取り実行
  - 同梱リファレンス戦略（動作確認用・既知の結果と突合できるもの）:
    a) sweet_spot 相当（単勝≥10×EV1.2-5.0近似条件）→ 既知の単ROI ≈ 1.19 と整合するか
    b) 全レース1番人気単勝ベタ買い → ROI ≈ 0.78-0.80（控除率近傍）になるか = **決済ロジックの健全性チェック**
    c) 三連複 モデル上位3頭BOX ベタ買い

## スコープ
- `backend/src/betting/backtest.py`（+ `__init__.py` 整備。T03/T04/T06 が同パッケージにモジュールを追加するので **既存ファイルを上書きしない**こと）
- `backend/scripts/roi100_backtest.py`
- `backend/tests/test_backtest_harness.py` — 決済の正誤（的中/不的中/同着・返還ケースの存在確認）・CI計算・正規化のユニットテスト（DBアクセスはモック or 小さな fixture）

## 注意
- 同着・返還（特払い）の存在を race_payouts 実データで監査し、扱いをコメントに明記（同着は複数行 payout が入っているはず）
- 本番DB読み取り専用・期間チャンク分割
- 出走取消馬を含む組合せの扱い（返還）を仕様化

## DoD
- `pytest tests/test_backtest_harness.py -q` 通過 / ruff 通過
- `roi100_backtest.py --strategy favorite_win --start 20250101 --end 20250630` で1番人気ベタ買いROIが 0.75〜0.85 に収まる（健全性チェック合格）
- CI付きレポートが出力される

## 完了報告に含めること
リファレンス戦略3本の結果（既知値との整合性）/ combination 表記の監査結果 / 同着・返還の扱い / 他タスクからの利用方法（コード例）
