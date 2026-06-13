# T01: エキゾチック発走前オッズの realtime 取得（馬連・ワイド・馬単・三連複・三連単）

## 目的
JV-Link 速報系でエキゾチックオッズ（0B32〜0B36 相当）を取得し `keiba.odds_history` / `keiba.latest_odds` に蓄積する。
組合せ単位のオーバーレイ検出（T07）の前提データ。現状 odds_history は win/place のみ（2026-03-28〜）。

## 現状の事実
- `windows-agent/jvlink_agent.py` の realtime は `0B31`（単複枠）のみポーリング
- `backend/src/importers/jvlink_parser.py:656-698 parse_odds()` は O1〜O6 レコードのパーサーが存在（O1=単複枠）
- `keiba.odds_history(race_id, bet_type, combination, odds, fetched_at)` / `keiba.latest_odds` は combination カラムを持つため**スキーマ変更不要の可能性が高い**（要確認。組合せ表記は「01-02」「01-02-03」形式等、既存 place の慣習に合わせる）
- JVDF 速報系 DataSpec: 0B32=馬連 / 0B33=ワイド / 0B34=馬単 / 0B35=三連複 / 0B36=三連単 **と推定**。必ず `jvlink_parser.py` のコメント・docs/・既存コードの仕様記述と突き合わせて確認し、確認結果をコードコメントに残すこと

## スコープ（変更可ファイル）
- `windows-agent/jvlink_agent.py`（realtime ループへの DataSpec 追加。ポーリング間隔は単複より粗くてよい: 発走 N 分前のみ取得する等、JV-Link 負荷とデータ量に配慮した設計を選ぶ）
- `backend/src/importers/jvlink_parser.py`（O2〜O6 パース内容の検証・不足あれば修正。三連単 O6 は組合せ数が最大4896点/レース — レコード構造を仕様に沿って確認）
- `backend/src/importers/odds_importer.py` 等オッズ取込経路（bet_type 拡張）
- `backend/src/api/` の import ルーター（受け口の bet_type バリデーション拡張が必要なら）
- 新規ユニットテスト: `backend/tests/test_exotic_odds_parser.py`

## 要件
1. realtime ループに 0B32〜0B36 のポーリングを追加（全レース×毎30秒は過剰。**発走前30分以内のレースに限定**するなどの絞りを実装）
2. パーサーは固定長レコードのサンプル文字列でユニットテスト（実JV-Link接続は不要）
3. odds_history への書き込みは既存 win/place と同経路・同冪等性（同一 race_id+bet_type+combination+fetched_at の重複防止）
4. データ量見積もりをコメントに明記（三連単全組合せ×36レース×頻度 → 1日あたり行数。1日1000万行を超える設計は不可。必要なら上位N人気組合せのみ格納等の削減策を提案実装）

## やらないこと
- Windows VM への配備・実稼働確認（後続で人が実施。配備手順を完了報告に書く）
- alembic migration（既存テーブルで足りる場合。足りない場合は migration ファイル作成のみ・**本番DBへの適用は絶対にしない**）

## DoD（検証方法）
- `cd backend && .venv/bin/python -m pytest tests/test_exotic_odds_parser.py -q` が通る
- 仕様サンプル（手作りでよい。バイト位置は仕様コメントと一致させる）から馬連〜三連単のパース結果が期待値一致
- ruff チェック通過: `.venv/bin/ruff check src/importers tests/test_exotic_odds_parser.py`

## 完了報告に含めること
変更ファイル一覧 / DataSpec の確認結果（推定が正しかったか）/ データ量見積もり / Windows VM 配備手順 / 残課題
