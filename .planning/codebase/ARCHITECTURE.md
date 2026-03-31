# Architecture

**Last Updated:** 2026-03-31

## Pattern Overview

**Overall:** 分散型マルチエージェント予測システム

**Key Characteristics:**
- **Windows Agent (JV-Link SDK)**: Parallels VM 上で JRA-VAN データを取得
- **Mac Backend (FastAPI)**: 指数算出エンジン (9 agents) + REST API + WebSocket
- **PostgreSQL (VPS)**: レースデータ・指数・成績の永続化
- **Next.js Frontend (PWA)**: 競馬新聞スタイルの Web UI、Google OAuth 認証
- **Index Agent Pattern**: 各指数計算は独立した Agent として実装

## System Layers

### Windows Agent (Data Acquisition)
- **目的**: JV-Link SDK 経由で JRA-VAN 生データを取得し FastAPI へ POST
- **場所**: `windows-agent/jvlink_agent.py`
- **依存**: pywin32 (COM), httpx, .env (JRAVAN_SID, BACKEND_URL)
- **モード**: setup / daily / realtime / recent

### Backend HTTP Layer (FastAPI)
- **目的**: Windows Agent からデータ受信、指数算出、REST API 提供
- **場所**: `backend/src/main.py`
- **主要ルート**:
  - `POST /api/import/*` — Windows Agent からの RA/SE/オッズデータ受信
  - `POST /api/changes/notify` — 出走取消・騎手変更検知 → 再算出
  - `GET /api/races/*` — レース/出馬表/成績データ取得
  - `GET /api/races/{id}/indices` — 指数・期待値取得
  - `WS /api/races/{id}/results/ws` — 成績リアルタイム配信

### Data Import Layer
- **場所**: `backend/src/importers/`
  - `jvlink_parser.py` — RA/SE/O1-O5/AV/JC バイナリ文字列のパース (SJIS)
  - `race_importer.py` — Race / RaceEntry / RaceResult の UPSERT
  - `odds_importer.py` — OddsHistory の UPSERT
  - `change_handler.py` — 変更検知 → 選択的再算出

### Index Calculation Agents
- **場所**: `backend/src/indices/`
- **実装済みエージェント**:

| Agent | ファイル | 重み |
|---|---|---|
| スピード指数 | `speed.py` | 0.30 |
| 上がり指数 | `last3f.py` | 0.12 |
| コース適性 | `course_aptitude.py` | 0.13 |
| 枠順バイアス | `frame_bias.py` | 0.06 |
| 騎手・調教師 | `jockey.py` | 0.08 |
| 展開指数 | `pace.py` | 0.08 |
| ローテーション | `rotation.py` | 0.05 |
| 穴ぐさ指数 | `anagusa.py` | — (sekito.anagusa 参照) |
| 複合指数 | `composite.py` | 全エージェント集約 |

### Database Layer
- **スキーマ**: `keiba.*` (メイン) + `sekito.*` (穴ぐさ等外部データ)
- **場所**: `backend/src/db/`
  - `models.py` — SQLAlchemy モデル群
  - `session.py` — Engine / SessionLocal / Alembic 統合
- **マイグレーション**: `backend/alembic/versions/`

### Frontend Layer (Next.js 16)
- **URL**: https://galloplab.com/kiseki/
- **認証**: Auth.js v5 / Google OAuth / JWT strategy
- **主要ページ**:
  - `/login` — Google ログイン画面
  - `/races?date=YYYYMMDD` — レース一覧（日付ナビ）
  - `/races/[id]` — レース詳細（指数・チャート・成績）
- **リアルタイム**: WebSocket で成績を受信 → チャート・テーブルを自動更新

## Data Flow

### 初期データ取込 (setup mode)
```
Windows Agent: JVOpen(option=3) → JVRead() → parse_ra/parse_se()
  → POST /api/import/races
  → RaceImporter.import_records() (UPSERT)
  → PostgreSQL keiba.races / race_entries
```

### 日次更新 + リアルタイム
```
Windows Agent: JVOpen(daily) → POST /api/import/*
  + JVRTOpen() → オッズ/取消/騎手変更をポーリング (30秒間隔)
  → POST /api/changes/notify → ChangeHandler → 選択的再算出
```

### 指数算出
```
CompositeIndexCalculator.calculate_batch(race_id)
  → 各エージェント.calculate_batch()
  → 重み付き合算
  → UPSERT keiba.calculated_indices (version管理)
```

### フロントエンド表示
```
Next.js SSR: fetchIndices() / fetchOdds() / fetchResults()
  → ProbabilityChart (ResizeObserver + Recharts BarChart)
  → IndicesTable (勝率/複勝率/期待値/穴ぐさバッジ)
  → RaceDetailClient: WebSocket → setResultsMap → 着順ハイライト更新
```

## Key Abstractions

### IndexCalculator (Abstract Base)
```python
class IndexCalculator(ABC):
    def calculate(self, race_id: int, horse_id: int) -> float: ...
    def calculate_batch(self, race_id: int) -> dict[int, float]: ...
```
- 全エージェントはこの基底クラスを継承
- 独立してテスト可能
- `version` カラムで再算出管理

### Importer Pattern (idempotent)
```python
insert_stmt = insert(Race).values(...).on_conflict_do_update(...)
```
- 重複実行しても安全（UPSERT）

### JV-Link Parser Pattern
- SJIS バイナリ文字列を Python dict に変換
- バイト位置は仕様書の 1-indexed を 0-indexed に変換
- 時刻フィールド: MSST (4B) / SST (3B) → 0.1秒単位整数

## Error Handling

- **パースエラー**: `None` を返してスキップ（クラッシュしない）
- **DBエラー**: UPSERT で冪等性を保証
- **未実装指数**: デフォルト値 50.0 を使用
- **WebSocket切断**: フロントエンドは再接続しない（成績確定後は不要）

## Authentication (Auth.js v5)

- **戦略**: JWT (セッションをサーバーに持たない)
- **認証フロー**: Google OAuth → `/auth/callback/google` → JWT → Cookie
- **既知の注意点**:
  - `signIn` / `signOut` はサーバーアクション経由（CSRF対策）
  - `useSession` は使用しない（`auth()` を直接呼ぶ）
  - `AUTH_URL` は `/auth` まで（`/api/auth` ではない）
  - コールバック URL: `https://galloplab.com/auth/callback/google`

---

*Updated: 2026-03-31*
