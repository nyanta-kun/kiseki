# kiseki - 競馬予測指数システム

JRA-VAN Data Labのデータを基に独自の競馬指数を算出し、オッズとの期待値比較で合理的な馬券購入判断を支援するシステム。

## 特徴

- **JV-Link SDK直接連携** — TARGETのGUI不要、全自動データ取得
- **14種の指数** — スピード/上がり/適性/展開/血統/騎手/調教/ローテーション等
- **リアルタイム対応** — 出走取消・騎手変更を即時検知→指数再算出
- **全券種オッズ** — 単勝〜三連単まで公式API経由で安定取得
- **期待値ベース分析** — 推定確率×オッズで単複期待値を算出・表示
- **競馬新聞PWA** — PC/スマホ対応レスポンシブWeb（https://galloplab.com/kiseki/）

## アーキテクチャ

```
Windows (Parallels)          Mac (Docker)                VPS
┌─────────────────┐    ┌───────────────────────┐    ┌──────────┐
│ Python 32bit    │    │ FastAPI Backend       │    │PostgreSQL│
│ + JV-Link SDK   │───▶│ 指数算出エンジン      │───▶│ keiba    │
│ (COM)           │HTTP│ REST API + WebSocket  │ SQL│ schema   │
└─────────────────┘    ├───────────────────────┤    └──────────┘
                       │ Next.js 16 Frontend   │
                       │ (PWA / galloplab.com) │
                       └───────────────────────┘
```

## 技術スタック

| レイヤー | 技術 |
|---|---|
| Frontend | Next.js 16 (App Router) / Tailwind CSS / Recharts |
| 認証 | Auth.js v5 (next-auth@beta) / Google OAuth |
| Backend | Python 3.12 / FastAPI / SQLAlchemy 2.0 |
| DB | PostgreSQL (VPS) / keiba スキーマ / Alembic |
| Windows Agent | Python 32bit / pywin32 / JV-Link COM |
| コンテナ | Docker / Docker Compose |
| パッケージ管理 | uv (Python) / pnpm (Node) |

## セットアップ

### 前提条件
- Mac (Apple Silicon or Intel)
- Parallels Desktop + Windows 10/11
- JRA-VAN Data Lab 会員（利用キー取得済み）
- VPS上のPostgreSQL（既存DB）

### 1. リポジトリクローン
```bash
git clone https://github.com/nyanta-kun/kiseki.git
cd kiseki
```

### 2. 環境変数設定
```bash
cp .env.example .env
# .env を編集（DB接続情報・JRA-VAN利用キー・Google OAuth等）
```

### 3. DBスキーマ作成
```bash
cd backend
uv sync
uv run alembic upgrade head
```

### 4. Backend起動（Mac側）
```bash
docker compose up -d  # FastAPI on port 8000
```

### 5. Frontend起動（ローカル開発）
```bash
cd frontend
pnpm install
pnpm dev  # http://localhost:3000/
```

### 6. Windows側（JV-Link Agent）初回セットアップ
```bash
# Parallels Windows上で実行（Python 32bit必須）
cd windows-agent
pip install -r requirements.txt

# 初回：過去データを一括取得（数時間かかる場合あり）
python jvlink_agent.py --mode setup

# 通常起動（日次更新＋リアルタイム監視）
python jvlink_agent.py --mode realtime
```

## デプロイ（galloplab.com）

```bash
# 通常デプロイ（フロント+バック、~1分）
bash scripts/deploy-galloplab.sh

# バックエンドのみ（Pythonソース変更、~15秒）
bash scripts/deploy-galloplab.sh --backend

# 完全再ビルド（依存関係変更時、~5分）
bash scripts/deploy-galloplab.sh --full
```

## 開発

```bash
# テスト実行
cd backend && uv run pytest

# コード品質チェック
cd backend && uv run ruff check .

# フロントエンドビルド確認
cd frontend && pnpm build
```

## ユーティリティ

```bash
# インポートデータを全クリア（パーサー修正後の再取得時）
cd backend && uv run python ../scripts/clear_imported_data.py
```

## 開発マイルストーン

| MS | 内容 | 状態 |
|---|---|---|
| MS1 | 環境構築・データ取込・スピード指数 | 完了 |
| MS2 | コース適性・枠順バイアス・総合指数 | 完了 |
| MS3 | 騎手・展開・血統・ローテーション指数 | 完了 |
| MS4 | パドック・調教・バックテスト | 一部完了 |
| MS5 | リアルタイム対応・変更検知 | 完了 |
| MS6 | 競馬新聞Web (PWA) | 完了 |
| MS7 | IPAT連携・収支管理 | 未着手 |
| MS8 | 全自動投票・継続最適化 | 未着手 |

## ライセンス

Private - All Rights Reserved
