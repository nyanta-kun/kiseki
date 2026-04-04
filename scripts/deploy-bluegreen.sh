#!/usr/bin/env bash
# Blue-Green デプロイスクリプト for galloplab.com
#
# 流れ:
#   Phase 1: 新イメージをビルド → 候補スロット（Bスロット: ports 3003/8004）で起動
#   Phase 2: 候補スロットのヘルスチェック + DBマイグレーション
#   Phase 3: 問題なければ本番スロット（Aスロット: ports 3002/8003）を切り替え
#   Phase 4: 候補スロットを停止・クリーンアップ
#
# 候補スロットがヘルスチェックに失敗した場合は本番を変更せず終了する（ゼロダウンタイム保護）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE_PROD="docker-compose.galloplab.yml"
COMPOSE_CAND="docker-compose.galloplab-b.yml"
CONTAINER_BACKEND_CAND="galloplab-backend-b"
CONTAINER_FRONTEND_CAND="galloplab-frontend-b"
CONTAINER_BACKEND_PROD="galloplab-backend-1"

log() { echo "[bluegreen] $*"; }
err() { echo "[bluegreen] ERROR: $*" >&2; }

# -------------------------------------------------------------------
# Phase 0: コード更新
# -------------------------------------------------------------------
log "Phase 0: コード更新..."
git fetch origin main
git reset --hard origin/main

# -------------------------------------------------------------------
# Phase 1: イメージビルド
# -------------------------------------------------------------------
log "Phase 1: イメージビルド（candidate タグ）..."
docker compose -f "$COMPOSE_PROD" build

log "Phase 1: 候補スロット起動（ports 3003/8004）..."
# 古い候補コンテナ・ネットワークが残っていれば強制削除
docker rm -f galloplab-backend-b galloplab-frontend-b 2>/dev/null || true
docker compose -f "$COMPOSE_CAND" down --remove-orphans 2>/dev/null || true
docker compose -f "$COMPOSE_CAND" up -d

# -------------------------------------------------------------------
# Phase 2: 候補スロット ヘルスチェック（最大3分）
# -------------------------------------------------------------------
log "Phase 2: 候補 backend ヘルスチェック待機..."
CAND_STATUS="unknown"
for i in $(seq 1 36); do
  sleep 5
  CAND_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_BACKEND_CAND" 2>/dev/null || echo "unknown")
  log "  backend-b: $CAND_STATUS ($i/36)"
  if [ "$CAND_STATUS" = "healthy" ]; then break; fi
done

if [ "$CAND_STATUS" != "healthy" ]; then
  err "候補 backend が healthy になりませんでした（最終: $CAND_STATUS）"
  err "本番は変更していません。"
  docker compose -f "$COMPOSE_CAND" logs --tail=60 backend-b >&2
  docker compose -f "$COMPOSE_CAND" down --remove-orphans
  exit 1
fi

log "Phase 2: 候補 frontend ヘルスチェック待機..."
CAND_FE_STATUS="unknown"
for i in $(seq 1 24); do
  sleep 5
  CAND_FE_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_FRONTEND_CAND" 2>/dev/null || echo "unknown")
  log "  frontend-b: $CAND_FE_STATUS ($i/24)"
  if [ "$CAND_FE_STATUS" = "healthy" ]; then break; fi
done

if [ "$CAND_FE_STATUS" != "healthy" ]; then
  err "候補 frontend が healthy になりませんでした（最終: $CAND_FE_STATUS）"
  err "本番は変更していません。"
  docker compose -f "$COMPOSE_CAND" logs --tail=60 frontend-b >&2
  docker compose -f "$COMPOSE_CAND" down --remove-orphans
  exit 1
fi

# -------------------------------------------------------------------
# Phase 2.5: DBマイグレーション（候補スロットで実行）
# -------------------------------------------------------------------
log "Phase 2.5: DBマイグレーション（候補スロット経由）..."
docker exec -e PYTHONPATH=/app "$CONTAINER_BACKEND_CAND" uv run alembic upgrade head

# -------------------------------------------------------------------
# Phase 3: 本番スロット切り替え
# -------------------------------------------------------------------
log "Phase 3: 本番スロット切り替え（ports 3002/8003）..."
log "  ※ イメージは Phase 1 でビルド済みのため高速切り替え"
docker compose -f "$COMPOSE_PROD" up -d --force-recreate

log "Phase 3: 本番 backend ヘルスチェック待機..."
PROD_STATUS="unknown"
for i in $(seq 1 24); do
  sleep 5
  PROD_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_BACKEND_PROD" 2>/dev/null || echo "unknown")
  log "  backend-1: $PROD_STATUS ($i/24)"
  if [ "$PROD_STATUS" = "healthy" ]; then break; fi
done

if [ "$PROD_STATUS" != "healthy" ]; then
  err "本番 backend の healthcheck 失敗（最終: $PROD_STATUS）"
  err "候補スロットはまだ動作中です。手動で確認してください。"
  docker compose -f "$COMPOSE_PROD" logs --tail=60 backend >&2
  # 候補スロットは残したままにする（手動ロールバック用）
  exit 1
fi

# -------------------------------------------------------------------
# Phase 4: クリーンアップ
# -------------------------------------------------------------------
log "Phase 4: 候補スロットをクリーンアップ..."
docker compose -f "$COMPOSE_CAND" down --remove-orphans

# 不要イメージを削除（Blue-Green で使用中の candidate タグイメージのみ残す）
#
# ダングリングイメージ: 前回ビルド時に candidate タグを上書きされた古いイメージ層。
#   → docker image prune -f で安全に削除（実行中コンテナから参照されていない）
#
# candidate タグ以外の未使用イメージ（ghcr.io/... や <none>:<none>）:
#   → docker image prune -a -f は実行中コンテナで使われていない全イメージを削除する。
#     ビルドキャッシュも消えるため、次回ビルドが遅くなる可能性があるが
#     VPSのディスク節約を優先してここでは全削除する。
#     ※ candidate タグ付きイメージは galloplab-backend-1 / galloplab-frontend-1 が
#       参照中のため prune -a でも削除されない。

log "Phase 4: 未使用イメージを削除..."
BEFORE=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo "不明")
docker image prune -a -f
AFTER=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo "不明")
log "  イメージ領域: $BEFORE → $AFTER"

docker compose -f "$COMPOSE_PROD" ps
log "=== デプロイ完了: https://galloplab.com/ ==="
