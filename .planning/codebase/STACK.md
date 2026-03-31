# Technology Stack

**Last Updated:** 2026-03-31

## Languages

- Python 3.12+ — Backend (FastAPI), Windows Agent, data importers, index calculators
- Python 3.x 32bit — Windows Agent (JV-Link COM integration必須)
- TypeScript — Frontend (Next.js 16, strict mode)

## Frontend

| 技術 | バージョン | 用途 |
|---|---|---|
| Next.js | 16.x (App Router) | SSR / SSG / PWA |
| React | 19.x | UI |
| Tailwind CSS | v4 | スタイリング |
| Recharts | 2.x | グラフ（勝率/複勝率） |
| Auth.js (next-auth) | v5 beta | Google OAuth 認証 |
| pnpm | 10.x | パッケージ管理 |

**ビルド設定:**
- `output: "standalone"` (Docker multi-stage)
- `node:22-alpine` ベースイメージ

## Backend

| 技術 | バージョン | 用途 |
|---|---|---|
| FastAPI | 0.115.0+ | REST API + WebSocket |
| SQLAlchemy | 2.0+ | ORM |
| Alembic | 1.14.0+ | DBマイグレーション |
| Pydantic | 2.9.0+ | バリデーション / 設定管理 |
| uv | 最新 | パッケージ管理 |

## Database

- PostgreSQL (VPS) — `keiba` スキーマ (メインデータ) + `sekito` スキーマ (穴ぐさ等)
- Alembic — DDL変更は必ずマイグレーション経由

## Windows Agent

- Python 3.x 32bit + pywin32 — JV-Link COM interface
- httpx — FastAPI への HTTP POST

## Deployment

| 環境 | URL | ポート | Compose ファイル |
|---|---|---|---|
| 本番 (galloplab.com) | https://galloplab.com/kiseki/ | frontend:3002 / backend:8003 | `docker-compose.galloplab.yml` |
| ローカル開発 | http://localhost:3000/ | frontend:3000 / backend:8000 | `docker-compose.yml` |

**デプロイスクリプト:** `scripts/deploy-galloplab.sh`

## Key Environment Variables

| 変数 | 説明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 接続文字列 |
| `BACKEND_URL` | SSR 用バックエンド URL（コンテナ内部、`/api` サフィックス込み） |
| `NEXT_PUBLIC_API_URL` | クライアント用バックエンド URL（nginx 経由） |
| `AUTH_SECRET` | Auth.js セッション暗号化キー |
| `AUTH_URL` | Auth.js コールバックベース（例: `https://galloplab.com/auth`） |
| `AUTH_GOOGLE_ID` / `AUTH_GOOGLE_SECRET` | Google OAuth 認証情報 |
| `JRAVAN_SID` | JRA-VAN SDK 利用キー |
| `CHANGE_NOTIFY_API_KEY` | Windows Agent → Backend 共有シークレット |

---

*Updated: 2026-03-31*
