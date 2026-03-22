# 🏇 kiseki - 競馬予測指数システム

JRA-VAN Data Labのデータを基に独自の競馬指数を算出し、オッズとの期待値比較で合理的な馬券購入判断を支援するシステム。

## 特徴

- **JV-Link SDK直接連携** — TARGETのGUI不要、全自動データ取得
- **14種の指数** — スピード/上がり/適性/展開/血統/騎手/調教/ローテーション等
- **リアルタイム対応** — 出走取消・騎手変更を即時検知→指数再算出
- **全券種オッズ** — 単勝〜三連単まで公式API経由で安定取得
- **期待値ベース投票** — 推定確率×オッズで期待値を算出
- **競馬新聞PWA** — PC/スマホ対応のレスポンシブWeb

## アーキテクチャ

```
Windows (Parallels)          Mac (Docker)              VPS
┌─────────────────┐    ┌──────────────────────┐    ┌──────────┐
│ Python 32bit    │    │ FastAPI Backend      │    │PostgreSQL│
│ + JV-Link SDK   │───▶│ 指数算出エンジン     │───▶│ keiba    │
│ (COM)           │HTTP│ REST API + WebSocket │ SQL│ schema   │
└─────────────────┘    ├──────────────────────┤    └──────────┘
                       │ Next.js Frontend     │
                       │ (PWA 競馬新聞)       │
                       └──────────────────────┘
```

## セットアップ

### 前提条件
- Mac (Apple Silicon or Intel)
- Parallels Desktop + Windows 10/11
- JRA-VAN Data Lab 会員（利用キー取得済み）
- VPS上のPostgreSQL（既存DB）

### 1. リポジトリクローン
```bash
git clone https://github.com/your-username/kiseki.git
cd kiseki
```

### 2. 環境変数設定
```bash
cp .env.example .env
# .env を編集し、DB接続情報とJRA-VAN利用キーを設定
```

### 3. DBスキーマ作成
```bash
cd backend
uv sync
uv run alembic upgrade head
```

### 4. Mac側（Backend）起動
```bash
cd backend
docker compose up -d  # FastAPI起動（ポート8000）
```

### 5. Windows側（JV-Link Agent）初回セットアップ
```bash
# Parallels Windows上で実行（Python 32bit必須）
cd windows-agent
pip install -r requirements.txt

# 初回：過去2年分のデータを一括取得（数時間かかる場合あり）
python jvlink_agent.py --mode setup

# 通常起動（日次更新＋リアルタイム監視）
python jvlink_agent.py
```

## 開発

```bash
# テスト実行
cd backend && .venv/bin/pytest

# コード品質チェック
cd backend && .venv/bin/ruff check .
```

## ユーティリティスクリプト

```bash
# インポートデータを全クリア（パーサー修正後の再取得時など）
cd backend && .venv/bin/python ../scripts/clear_imported_data.py
```

## ライセンス

Private - All Rights Reserved
