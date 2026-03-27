#!/bin/bash
# sekito-stable.com デプロイスクリプト
# 実行: bash scripts/deploy-sekito.sh
#
# 事前準備:
#   1. sekito側に ~/GitHub/kiseki が存在すること（git cloneまたはpull済み）
#   2. ~/GitHub/kiseki/.env が設定済みであること
#   3. nginxに /kiseki 設定が追加済みであること
#   4. nginxの /kiseki/api/ location に WebSocket ヘッダーが設定済みであること:
#      proxy_http_version 1.1;
#      proxy_set_header Upgrade $http_upgrade;
#      proxy_set_header Connection "upgrade";

set -euo pipefail

REMOTE_HOST="sekito"
REMOTE_DIR="~/GitHub/kiseki"
COMPOSE_FILE="docker-compose.sekito.yml"

echo "[deploy] sekito-stable.com へデプロイ開始"

# Step 1: ローカルでビルドチェック（任意）
# echo "[deploy] ローカルでビルド確認..."

# Step 2: リモートでgit pullしてビルド・起動
ssh "$REMOTE_HOST" bash << EOF
  set -euo pipefail
  cd $REMOTE_DIR

  echo "[deploy] git pull..."
  git pull origin main

  echo "[deploy] 不要なDockerリソースを削除..."
  docker image prune -f

  echo "[deploy] ビルドして起動..."
  docker compose -f $COMPOSE_FILE up -d --build

  echo "[deploy] ヘルスチェック待機..."
  sleep 10
  docker ps | grep kiseki

  echo "[deploy] 完了"
EOF

echo "[deploy] デプロイ完了: https://sekito-stable.com/kiseki/"
