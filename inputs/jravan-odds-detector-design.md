# JRA-VAN DATALAB 大口投票検出システム 設計書

## 1. 概要

### 1.1 目的

JRA-VAN DATALABが提供するオッズ・票数データを活用し、前日発売開始直後に人気薄の馬に対する大口投票（異常投票）があったかどうかを検出・分析するシステムを構築する。

### 1.2 システム構成

```
[Windows PC]                      [Mac / 分析環境]
  JV-Link (32bit COM)              Python 分析スクリプト
       |                                  |
  FastAPI プロキシサーバー ──HTTP──→ データ取得クライアント
  (Python 32bit)                          |
       |                           SQLite / PostgreSQL
  JRA-VAN サーバー                        |
                                   異常投票検出エンジン
                                          |
                                   レポート / アラート
```

### 1.3 対象データ

| 用途 | データ種別ID | 名称 | レコード種別ID | 取得API |
|------|-------------|------|---------------|---------|
| 速報オッズ（単複枠） | 0B31 | 速報オッズ（単複枠） | O1 | JVRTOpen |
| 速報オッズ（全賭式） | 0B30 | 速報オッズ（全賭式） | O1〜O6 | JVRTOpen |
| 時系列オッズ（単複枠） | 0B41 | 時系列オッズ（単複枠） | O1 | JVRTOpen |
| 時系列オッズ（馬連） | 0B42 | 時系列オッズ（馬連） | O2 | JVRTOpen |
| 速報票数 | 0B20 | 速報票数 | H1 | JVRTOpen |
| レース詳細 | 0B15 | 速報レース情報 | RA | JVRTOpen |

---

## 2. データ収集方法

### 2.1 過去データの収集（事後分析用）

#### 2.1.1 時系列オッズ（0B41 / 0B42）

過去レースの時系列オッズスナップショットを一括取得する。これが大口投票の事後検出における主要データソースとなる。

**取得方法:**

```
API: JVRTOpen("0B41", key)
key: YYYYMMDD（日付単位）またはYYYYMMDDJJKKNNRR（レース単位）
```

- JV-Data仕様書上の提供保証期間: 1年間
- 実態: 2003年10月4日以降のデータが取得可能（保証外）
- レコード種別: O1（単複枠連オッズと同一フォーマット）
- 「発表月日時分」フィールドが各スナップショットの時刻を示す
- 1レースあたり複数のO1レコードが時刻順に返される

**収集フロー:**

```
1. 対象日付リストを作成（例: 過去1年分の開催日）
2. 各日付に対して JVRTOpen("0B41", "YYYYMMDD") を実行
3. JVRead ループで全O1レコードを取得
4. レコードをパースし、レースキー + 発表時刻 をキーにDBへ格納
5. JVClose で終了
```

#### 2.1.2 確定票数（0B20）

確定後の最終票数データ。基準値として使用する。

**取得方法:**

```
API: JVRTOpen("0B20", key)
key: YYYYMMDD（日付単位）
```

- 前日売最終データおよび確定後データのみ提供
- 発売中の中間票数は取得不可（速報オッズから推定する必要あり）

#### 2.1.3 レース情報（0B15）

出走馬情報・発走時刻の取得用。

```
API: JVRTOpen("0B15", key)
key: YYYYMMDD（日付単位）
レコード種別: RA（レース詳細）, SE（馬毎レース情報）
```

### 2.2 開催週のリアルタイム収集

#### 2.2.1 データ提供タイミング

| タイミング | データ種別 | 備考 |
|-----------|-----------|------|
| 金曜 19:00〜 | 0B31（単複枠） | PAT前日発売がある場合 |
| 土曜 朝〜 | 0B31, 0B30 | 当日発売開始後 |
| 日曜 朝〜 | 0B31, 0B30 | 当日発売開始後 |
| 確定後 | 0B20 | 前日売最終 + 確定票数 |

#### 2.2.2 オッズ更新間隔

JRA-VANサーバ側は10秒ごとにローテーション方式でオッズを更新する:

```
ローテーション順序:
  1. 当回レース（次発走レース）のオッズ
  2. 当回以外のレースのオッズ
  3. 当回以外のレースのオッズ
  4. 翌日前売りレースのオッズ
  → 各競馬場ごとにこの順番をローテーション
```

| 条件 | 更新間隔（目安） |
|------|----------------|
| 1レースのみ発売 | 約10秒 |
| 2場開催 当回レース | 約20〜40秒 |
| 3場開催 + 前売り（最大） | 当回: 約120秒 / その他: 約600〜700秒 |
| 前日発売（金曜夜〜） | 数分〜10分程度 |

#### 2.2.3 収集スケジュール設計

前日発売の大口投票検出が目的のため、金曜夜〜土曜朝の収集が最も重要。

```
■ 金曜日
  19:00〜23:59  5分間隔で全レースのオッズ取得
  
■ 土曜日
  00:00〜06:00  10分間隔で全レースのオッズ取得
  06:00〜09:00  5分間隔
  09:00〜発走   3分間隔（当日分は速報で高頻度取得）
  
■ 日曜日
  同上パターン
```

#### 2.2.4 FastAPIプロキシ エンドポイント設計

Windows側のFastAPIプロキシに以下のエンドポイントを実装する:

```
GET  /api/odds/realtime/{race_id}
     → 0B31 で指定レースの現在オッズを取得

GET  /api/odds/timeseries/{date}
     → 0B41 で指定日の時系列オッズを一括取得

GET  /api/odds/timeseries/{race_id}
     → 0B41 で指定レースの時系列オッズを取得

GET  /api/votes/final/{date}
     → 0B20 で確定票数を取得

GET  /api/race/info/{date}
     → 0B15 でレース情報・出走馬一覧を取得
```

---

## 3. O1レコード バイト構造マッピング

### 3.1 レコード全体構造

O1レコードは単勝・複勝・枠連のオッズデータを含む固定長テキスト形式のレコードである。

**注意:** 以下は開発者コミュニティの実装例・ブログ記事から復元した推定構造である。正確なバイト位置はJV-Data仕様書（JV-Data490.pdf 等）のP12「オッズ１（単複枠）」を必ず参照すること。

### 3.2 ヘッダ部（共通）

| # | 項目名 | 開始位置 | 長さ | 型 | 説明 |
|---|-------|---------|------|---|----|
| 1 | レコード種別ID | 1 | 2 | 文字 | "O1" 固定 |
| 2 | データ区分 | 3 | 1 | 数字 | 1:中間, 2:前日最終, 4:確定オッズ, 5:確定(特), 6:確定(特払), 9:レース中止 |
| 3 | データ作成年月日 | 4 | 8 | 数字 | YYYYMMDD |
| 4 | 開催年月日 | 12 | 8 | 数字 | YYYYMMDD |
| 5 | 競馬場コード | 20 | 2 | 数字 | 01:札幌, 02:函館, ..., 10:小倉 |
| 6 | 開催回 | 22 | 2 | 数字 | 第XX回 |
| 7 | 開催日目 | 24 | 2 | 数字 | X日目 |
| 8 | レース番号 | 26 | 2 | 数字 | 01〜12 |
| 9 | 発表月日時分 | 28 | 8 | 数字 | MMDDHHmm（時系列キー） |
| 10 | 登録頭数 | 36 | 2 | 数字 | |
| 11 | 出走頭数 | 38 | 2 | 数字 | |
| 12 | 発売フラグ(単勝) | 40 | 1 | 数字 | 7:発売あり |
| 13 | 発売フラグ(複勝) | 41 | 1 | 数字 | 7:発売あり |
| 14 | 発売フラグ(枠連) | 42 | 1 | 数字 | 7:発売あり |
| 15 | 複勝着払キー | 43 | 1 | 数字 | 2:2着まで, 3:3着まで |

### 3.3 単勝オッズ部

ヘッダ直後（推定44バイト目〜）から28頭分のデータ領域。

**1頭あたり構造（推定8バイト）:**

| 項目名 | 長さ | 型 | 説明 |
|-------|------|---|----|
| 馬番 | 2 | 数字 | 01〜28 |
| 単勝オッズ | 4 | 数字 | 10倍表示（0571 = 5.7倍） |
| 人気順 | 2 | 数字 | 01〜28 |

- 28頭分 × 8バイト = 224バイト
- 未出走枠はスペース(0x20)で埋められる
- 取消馬はオッズ・人気部分が`**`(0x2A)で埋められる

### 3.4 複勝オッズ部

単勝部の後に28頭分。

**1頭あたり構造（推定12バイト）:**

| 項目名 | 長さ | 型 | 説明 |
|-------|------|---|----|
| 馬番 | 2 | 数字 | |
| 複勝最低オッズ | 4 | 数字 | 10倍表示 |
| 複勝最高オッズ | 4 | 数字 | 10倍表示 |
| 人気順 | 2 | 数字 | |

- 28頭分 × 12バイト = 336バイト

### 3.5 枠連オッズ部

複勝部の後。枠番の組み合わせ（最大36通り）。

**1組あたり構造（推定9バイト）:**

| 項目名 | 長さ | 型 | 説明 |
|-------|------|---|----|
| 枠番組(枠1) | 1 | 数字 | |
| 枠番組(枠2) | 1 | 数字 | |
| 枠連オッズ | 5 | 数字 | 10倍表示 |
| 人気順 | 2 | 数字 | |

### 3.6 票数データの所在

**重要:** O1レコード自体にはオッズと人気順のみが含まれ、馬番別の個別票数は直接含まれない。

票数の取得方法は以下の通り:

| データ | 取得元 | 利用可能タイミング |
|-------|-------|-----------------|
| 各賭式の票数合計 | 速報オッズ (0B31) 内のフィールド | 発売中〜確定後 |
| 馬番別の個別票数 | 速報票数 (0B20) の H1レコード | 前日売最終 + 確定後のみ |
| 中間時点の推定票数 | オッズから逆算 | 発売中（推定値） |

### 3.7 H1レコード（票数1）の票数フィールド

確定票数データ(0B20)のH1レコード内には、馬番ごとの票数が格納される。

**単勝票数フィールド構造（1頭あたり15バイト）:**

| 項目名 | 長さ | 型 | 説明 |
|-------|------|---|----|
| 馬番 | 2 | 数字 | |
| 票数 | 11 | 数字 | 単位: 票（100円 = 1票） |
| 人気順 | 2 | 数字 | |

- 28頭分が連結: `UUFFFFFFFFFFFFFFFNN` × 28 = 420バイト
- 例: `0100000052008` → 馬番01, 票数52008票, 人気不明（後続2桁）

### 3.8 オッズから票数を逆算する方法

中間時点で馬番別票数が提供されないため、オッズから推定する。

```
パリミュチュエル方式の基本式:
  オッズ = (総票数 × 控除率補数) / 当該馬票数

  単勝の場合:
    控除率 = 20% → 控除率補数 = 0.80
    当該馬の推定票数 = (総票数 × 0.80) / オッズ

  ※ オッズは10円単位に切り捨てされるため、完全な逆算は不可能
  ※ 特に低オッズ（人気馬）では誤差が小さく、高オッズ（人気薄）では誤差が大きい
```

---

## 4. データベース設計

### 4.1 テーブル定義

```sql
-- レース情報
CREATE TABLE races (
    race_key        TEXT PRIMARY KEY,  -- YYYYMMDD + 競馬場コード + 回 + 日目 + レース番号
    race_date       DATE NOT NULL,
    course_code     TEXT NOT NULL,     -- 競馬場コード
    kai             INTEGER,           -- 開催回
    nichi           INTEGER,           -- 開催日目
    race_no         INTEGER NOT NULL,
    race_name       TEXT,
    head_count      INTEGER,           -- 出走頭数
    post_time       TEXT               -- 発走時刻
);

-- 時系列オッズスナップショット
CREATE TABLE odds_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_key        TEXT NOT NULL,
    snapshot_time   TEXT NOT NULL,      -- MMDDHHmm（発表月日時分）
    snapshot_ts     TIMESTAMP NOT NULL, -- パース済みタイムスタンプ
    data_kubun      INTEGER,           -- データ区分
    raw_record      TEXT,              -- 生レコード（デバッグ用）
    UNIQUE(race_key, snapshot_time)
);

-- 馬番別オッズ（スナップショットごと）
CREATE TABLE horse_odds (
    snapshot_id     INTEGER NOT NULL,
    umaban          INTEGER NOT NULL,  -- 馬番
    win_odds        REAL,              -- 単勝オッズ（実数値）
    win_popularity  INTEGER,           -- 単勝人気順
    place_odds_min  REAL,              -- 複勝最低オッズ
    place_odds_max  REAL,              -- 複勝最高オッズ
    place_popularity INTEGER,          -- 複勝人気順
    est_win_votes   INTEGER,           -- 推定単勝票数（逆算値）
    est_place_votes INTEGER,           -- 推定複勝票数（逆算値）
    PRIMARY KEY (snapshot_id, umaban),
    FOREIGN KEY (snapshot_id) REFERENCES odds_snapshots(id)
);

-- 確定票数（0B20 由来）
CREATE TABLE final_votes (
    race_key        TEXT NOT NULL,
    umaban          INTEGER NOT NULL,
    win_votes       INTEGER,           -- 単勝確定票数
    win_popularity  INTEGER,
    place_votes     INTEGER,           -- 複勝確定票数
    place_popularity INTEGER,
    PRIMARY KEY (race_key, umaban)
);

-- 異常投票検出結果
CREATE TABLE anomaly_detections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    race_key        TEXT NOT NULL,
    umaban          INTEGER NOT NULL,
    detection_type  TEXT NOT NULL,      -- 'LARGE_BET', 'ODDS_CRASH', 'VOTE_SPIKE'
    severity        TEXT NOT NULL,      -- 'LOW', 'MEDIUM', 'HIGH'
    snapshot_from   TEXT NOT NULL,      -- 検出区間 開始時刻
    snapshot_to     TEXT NOT NULL,      -- 検出区間 終了時刻
    odds_before     REAL,
    odds_after      REAL,
    odds_change_pct REAL,              -- オッズ変動率(%)
    est_vote_delta  INTEGER,           -- 推定投入票数
    est_amount_yen  INTEGER,           -- 推定投入金額(円)
    win_odds_at_detection REAL,        -- 検出時点のオッズ（人気薄判定用）
    final_result    TEXT,              -- 'WIN', 'PLACE', 'OUT'（結果紐付け）
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 4.2 インデックス

```sql
CREATE INDEX idx_snapshots_race ON odds_snapshots(race_key);
CREATE INDEX idx_snapshots_time ON odds_snapshots(race_key, snapshot_ts);
CREATE INDEX idx_horse_odds_snapshot ON horse_odds(snapshot_id);
CREATE INDEX idx_anomaly_race ON anomaly_detections(race_key);
CREATE INDEX idx_anomaly_severity ON anomaly_detections(severity);
```

---

## 5. 異常投票検出ロジック

### 5.1 検出アルゴリズム概要

2つの連続するスナップショット間のオッズ変動を分析し、以下の条件に合致するケースを異常投票として検出する。

### 5.2 検出条件

#### 条件A: オッズ急落検出（ODDS_CRASH）

```
対象: 単勝オッズ 30.0倍以上の馬（人気薄）
条件: 2つのスナップショット間でオッズが20%以上下落
       AND 下落幅が5.0倍以上

例: 80.0倍 → 55.0倍（31.25%下落, 25.0倍下落）→ HIGH
    50.0倍 → 38.0倍（24.0%下落, 12.0倍下落） → MEDIUM
```

#### 条件B: 推定票数急増検出（VOTE_SPIKE）

```
対象: 全馬
条件: 推定票数の増加分が、同区間の全馬合計増加分の30%以上を占める
       AND 当該馬のオッズが直前時点で20.0倍以上

  集中度 = 当該馬の推定票数増分 / 全馬合計の推定票数増分
  集中度 ≥ 0.30 → 検出
```

#### 条件C: 前日発売特化検出（LARGE_BET）

```
対象: 金曜19:00〜土曜09:00 の区間
条件: 条件Aまたは条件Bに該当
       AND 発売開始からの経過時間が3時間以内
       
重み付け: 発売開始直後ほどスコアを高くする
```

### 5.3 重要度判定

| 重要度 | 条件 |
|-------|------|
| HIGH | オッズ下落率 30%以上、または集中度 0.50以上 |
| MEDIUM | オッズ下落率 20〜30%、または集中度 0.30〜0.50 |
| LOW | 上記に近いが閾値未満のボーダーケース |

### 5.4 推定投入金額の算出

```python
def estimate_bet_amount(odds_before, odds_after, total_votes_before, total_votes_after):
    """
    2時点のオッズと総票数から、当該馬への推定投入票数を算出する。
    
    パリミュチュエル方式:
      odds = (total_votes * 0.80) / horse_votes
      → horse_votes = (total_votes * 0.80) / odds
    """
    payout_rate = 0.80  # 単勝控除率 20%
    
    votes_before = (total_votes_before * payout_rate) / odds_before
    votes_after  = (total_votes_after  * payout_rate) / odds_after
    
    vote_delta = votes_after - votes_before
    amount_yen = vote_delta * 100  # 1票 = 100円
    
    return int(vote_delta), int(amount_yen)
```

---

## 6. 実装モジュール構成

### 6.1 Windows側（FastAPIプロキシ）

```
jvlink-proxy/
├── main.py                   # FastAPI アプリケーション
├── jvlink_client.py          # JV-Link COM操作ラッパー
├── parsers/
│   ├── __init__.py
│   ├── o1_parser.py          # O1レコードパーサー
│   ├── h1_parser.py          # H1レコード（票数）パーサー
│   └── ra_parser.py          # RAレコード（レース情報）パーサー
├── models.py                 # Pydanticモデル定義
└── config.py                 # 設定（ポート番号、JV-Link初期化キー等）
```

### 6.2 分析側（Mac / クロスプラットフォーム）

```
odds-anomaly-detector/
├── collector/
│   ├── __init__.py
│   ├── proxy_client.py       # FastAPIプロキシへのHTTPクライアント
│   ├── timeseries_collector.py  # 時系列データ収集（過去分一括）
│   └── realtime_collector.py    # リアルタイム定期収集
├── analyzer/
│   ├── __init__.py
│   ├── odds_calculator.py    # オッズ→票数逆算
│   ├── anomaly_detector.py   # 異常検出エンジン
│   └── result_matcher.py     # レース結果との紐付け
├── storage/
│   ├── __init__.py
│   ├── database.py           # SQLAlchemy / SQLiteアクセス
│   └── migrations/           # Alembicマイグレーション
├── reporter/
│   ├── __init__.py
│   ├── html_report.py        # HTML分析レポート生成
│   └── slack_notifier.py     # Slack通知（リアルタイム検出時）
├── scheduler.py              # APScheduler による定期実行
├── config.yaml               # 閾値・スケジュール等の設定
└── main.py                   # エントリーポイント
```

---

## 7. 処理フロー

### 7.1 過去データ分析フロー

```
[開始]
  │
  ├─ 1. 対象日付範囲を指定
  │
  ├─ 2. レース情報取得 (0B15)
  │     → races テーブルへ格納
  │
  ├─ 3. 時系列オッズ取得 (0B41)
  │     → 日付単位でJVRTOpen
  │     → O1レコードをパース
  │     → odds_snapshots + horse_odds テーブルへ格納
  │
  ├─ 4. 確定票数取得 (0B20)
  │     → H1レコードをパース
  │     → final_votes テーブルへ格納
  │
  ├─ 5. 異常検出エンジン実行
  │     → 各レースの連続スナップショット間を比較
  │     → 条件A/B/Cで判定
  │     → anomaly_detections テーブルへ格納
  │
  ├─ 6. レース結果紐付け
  │     → 検出された馬の着順を確認
  │     → final_result カラムを更新
  │
  └─ 7. レポート生成
        → 検出精度・的中率等の統計レポート
[終了]
```

### 7.2 リアルタイム監視フロー

```
[スケジューラ起動]
  │
  ├─ 金曜 18:55  レース情報取得 → DB格納
  │
  ├─ 金曜 19:00〜 定期ポーリング開始
  │     │
  │     └─ N分間隔で実行:
  │          1. 全対象レースの速報オッズ取得 (0B31)
  │          2. O1レコードパース → odds_snapshots + horse_odds
  │          3. 前回スナップショットと比較
  │          4. 異常検出条件に合致 → anomaly_detections + Slack通知
  │
  ├─ 土曜/日曜  同様のポーリング（間隔調整）
  │
  └─ 各レース確定後  確定票数取得 → 結果紐付け
[終了]
```

---

## 8. 設定パラメータ

```yaml
# config.yaml
proxy:
  host: "192.168.1.xxx"    # Windows PC の IP
  port: 8000

collection:
  # 前日発売ポーリング間隔（分）
  friday_night_interval: 5
  saturday_early_interval: 10
  saturday_morning_interval: 5
  raceday_interval: 3

detection:
  # 条件A: オッズ急落
  odds_crash:
    min_initial_odds: 30.0       # 対象: この倍率以上の馬
    min_drop_pct: 0.20           # 最低下落率 20%
    min_drop_abs: 5.0            # 最低下落幅 5.0倍
  
  # 条件B: 票数集中
  vote_spike:
    min_initial_odds: 20.0       # 対象: この倍率以上の馬
    min_concentration: 0.30      # 最低集中度 30%
  
  # 条件C: 前日発売特化
  pre_sale:
    enabled: true
    early_hours_boost: 3         # 発売開始から3時間以内はスコア加算
    boost_factor: 1.5

  # 重要度閾値
  severity:
    high_drop_pct: 0.30
    high_concentration: 0.50
    medium_drop_pct: 0.20
    medium_concentration: 0.30

notification:
  slack_webhook_url: "https://hooks.slack.com/services/xxx"
  notify_severity: ["HIGH", "MEDIUM"]

database:
  url: "sqlite:///odds_anomaly.db"
```

---

## 9. 制約事項・注意点

### 9.1 技術的制約

| 項目 | 内容 |
|------|------|
| JV-Link動作環境 | Windows専用、32bit COM | 
| Python版数 | JV-Link利用時は32bit版Python必須 |
| オッズ更新頻度 | 前日発売時は数分〜10分間隔（場数・レース消化状況依存） |
| 中間票数 | 0B20は前日売最終+確定のみ。発売中はオッズから逆算 |
| 逆算精度 | オッズの10円切り捨てにより推定票数に誤差あり |
| 時系列データ保証期間 | 仕様書上は1年（実態はそれ以上取得可能だが保証外） |

### 9.2 分析上の注意

- オッズの急変は大口投票以外にも発生する（出走取消、騎手変更、天候急変など）
- 前日発売は総票数が少ないため、比較的小額でもオッズが大きく動く場合がある
- 「大口」の定義は総票数に対する相対値で判断すべき
- レコード種別のバイト構造は推定値を含むため、実データでの検証が必須

### 9.3 今後の拡張候補

- 馬連・三連単のオッズ急変検出（0B30 / 0B42）
- 過去の検出パターンに基づく機械学習モデルの導入
- レース結果との相関分析ダッシュボード
- cc-server (Ubuntu) でのcron自動実行

---

## 10. 実装優先順位

| Phase | 内容 | 優先度 |
|-------|------|-------|
| Phase 1 | O1レコードパーサー実装 + 実データでバイト構造検証 | ★★★ |
| Phase 2 | FastAPIプロキシ オッズ取得エンドポイント | ★★★ |
| Phase 3 | DB設計 + 時系列オッズ収集パイプライン | ★★★ |
| Phase 4 | 異常検出エンジン（条件A: オッズ急落） | ★★☆ |
| Phase 5 | 票数逆算ロジック + 条件B検出 | ★★☆ |
| Phase 6 | リアルタイム監視 + Slack通知 | ★☆☆ |
| Phase 7 | レポート生成 + 精度分析 | ★☆☆ |

---

*文書作成日: 2026-03-29*
*バージョン: 1.0*
