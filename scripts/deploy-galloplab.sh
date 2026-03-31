#!/bin/bash
# galloplab.com デプロイスクリプト
# 実行: bash scripts/deploy-galloplab.sh [オプション]
#
# オプション:
#   (なし)      通常デプロイ。Dockerキャッシュを活用（~1分）
#   --backend   バックエンドのみ再起動。Pythonソース変更時に使用（~15秒）
#   --full      完全再ビルド。依存関係(package.json/pyproject.toml)変更時（~5分）
#
# 事前準備:
#   1. sekito側に ~/GitHub/kiseki が存在すること（git cloneまたはpull済み）
#   2. ~/GitHub/kiseki/.env が設定済みであること（AUTH_URLはgalloplab.comに設定不要）
#   3. nginxに galloplab.com 設定が追加済みであること
#   4. galloplab.com のSSL証明書が取得済みであること（certbot）
#
# ポート割り当て:
#   frontend: 127.0.0.1:3002
#   backend:  127.0.0.1:8003

set -euo pipefail

REMOTE_HOST="sekito"
REMOTE_DIR="~/GitHub/kiseki"
COMPOSE_FILE="docker-compose.galloplab.yml"
MODE="${1:-normal}"

echo "[deploy] galloplab.com へデプロイ開始 (mode: $MODE)"

case "$MODE" in

  --backend)
    # -------------------------------------------------------
    # バックエンドのみ再起動
    # src/ はvolumeマウントされているので git pull + restart だけでOK
    # -------------------------------------------------------
    echo "[deploy] バックエンド再起動のみ（~15秒）"
    ssh "$REMOTE_HOST" bash << EOF
      set -euo pipefail
      cd $REMOTE_DIR
      git pull origin main
      docker compose -f $COMPOSE_FILE restart backend
      echo "[deploy] バックエンド再起動完了"
EOF
    ;;

  --full)
    # -------------------------------------------------------
    # 完全再ビルド（--no-cache）
    # package.json / pyproject.toml 変更時に使用
    # -------------------------------------------------------
    echo "[deploy] 完全再ビルド（~5分）"
    ssh "$REMOTE_HOST" bash << EOF
      set -euo pipefail
      cd $REMOTE_DIR
      git pull origin main
      docker image prune -f
      docker compose -f $COMPOSE_FILE build --no-cache
      echo "[deploy] DBマイグレーション実行..."
      docker compose -f $COMPOSE_FILE run --rm --no-deps -e PYTHONPATH=/app backend uv run alembic upgrade head
      docker compose -f $COMPOSE_FILE up -d
      sleep 10
      docker ps | grep galloplab
      echo "[deploy] 完了"
EOF
    ;;

  normal|*)
    # -------------------------------------------------------
    # 通常デプロイ（Dockerキャッシュ活用）
    # ソースコード変更のみならフロント~1分、バック~20秒
    # -------------------------------------------------------
    echo "[deploy] 通常デプロイ（Dockerキャッシュ使用、~1分）"
    ssh "$REMOTE_HOST" bash << EOF
      set -euo pipefail
      cd $REMOTE_DIR
      git pull origin main
      echo "[deploy] DBマイグレーション実行..."
      docker compose -f $COMPOSE_FILE build backend
      docker compose -f $COMPOSE_FILE run --rm --no-deps -e PYTHONPATH=/app backend uv run alembic upgrade head
      docker compose -f $COMPOSE_FILE up -d --build
      sleep 8
      docker ps | grep galloplab
      echo "[deploy] 完了"
EOF
    ;;

esac

echo "[deploy] デプロイ完了: https://galloplab.com/kiseki/"
