#!/usr/bin/env bash
# Blue-Green デプロイスクリプト for galloplab.com
#
# 流れ:
#   Phase 0: コード更新 + ghcr.io から最新イメージを pull
#   Phase 1: 候補スロット（Bスロット: ports 3003/8004）で起動・ヘルスチェック
#   Phase 2: 候補スロットのヘルスチェック
#   Phase 3: 問題なければ本番スロット（Aスロット: ports 3002/8003）を切り替え
#   Phase 3.5: DBマイグレーション（本番スロットで実行・DBはVPS内のみアクセス可）
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
# Phase 0: コード更新 + イメージ取得
# -------------------------------------------------------------------
log "Phase 0: コード更新..."
git fetch origin main
git reset --hard origin/main

log "Phase 0: ghcr.io ログイン..."
# GHCR_TOKEN / GHCR_USER は .env に設定
# 例: GHCR_USER=nyanta-kun / GHCR_TOKEN=ghp_xxxxx (read:packages スコープ)
GHCR_TOKEN=$(grep -E '^GHCR_TOKEN=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
GHCR_USER=$(grep -E '^GHCR_USER=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
if [ -n "$GHCR_TOKEN" ] && [ -n "$GHCR_USER" ]; then
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
else
  log "  警告: GHCR_TOKEN / GHCR_USER が未設定のため docker login をスキップ"
  log "  パブリックパッケージの場合は問題なし"
fi

log "Phase 0: 最新イメージを pull..."
docker compose -f "$COMPOSE_PROD" pull

# -------------------------------------------------------------------
# Phase 1: 候補スロット起動（ports 3003/8004）
# -------------------------------------------------------------------
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
for i in $(seq 1 90); do
  # 先に判定してから待つ。既に healthy なら待たずに抜ける
  CAND_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_BACKEND_CAND" 2>/dev/null || echo "unknown")
  if [ "$CAND_STATUS" = "healthy" ]; then log "  backend-b: healthy ($i/90)"; break; fi
  [ $((i % 5)) -eq 1 ] && log "  backend-b: $CAND_STATUS ($i/90)"
  sleep 2
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
for i in $(seq 1 60); do
  # 先に判定してから待つ。既に healthy なら待たずに抜ける
  CAND_FE_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_FRONTEND_CAND" 2>/dev/null || echo "unknown")
  if [ "$CAND_FE_STATUS" = "healthy" ]; then log "  frontend-b: healthy ($i/60)"; break; fi
  [ $((i % 5)) -eq 1 ] && log "  frontend-b: $CAND_FE_STATUS ($i/60)"
  sleep 2
done

if [ "$CAND_FE_STATUS" != "healthy" ]; then
  err "候補 frontend が healthy になりませんでした（最終: $CAND_FE_STATUS）"
  err "本番は変更していません。"
  docker compose -f "$COMPOSE_CAND" logs --tail=60 frontend-b >&2
  docker compose -f "$COMPOSE_CAND" down --remove-orphans
  exit 1
fi

# -------------------------------------------------------------------
# Phase 3: 本番スロット切り替え
# -------------------------------------------------------------------
log "Phase 3: 本番スロット切り替え（ports 3002/8003）..."
log "  ※ イメージは Phase 0 で pull 済みのため高速切り替え"
# compose 管理外（手動起動等）の同名コンテナを先に削除してコンフリクトを防ぐ
docker rm -f galloplab-backend-1 galloplab-frontend-1 2>/dev/null || true
docker compose -f "$COMPOSE_PROD" up -d --force-recreate

log "Phase 3: 本番 backend ヘルスチェック待機..."
PROD_STATUS="unknown"
for i in $(seq 1 60); do
  # 先に判定してから待つ。既に healthy なら待たずに抜ける
  PROD_STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_BACKEND_PROD" 2>/dev/null || echo "unknown")
  if [ "$PROD_STATUS" = "healthy" ]; then log "  backend-1: healthy ($i/60)"; break; fi
  [ $((i % 5)) -eq 1 ] && log "  backend-1: $PROD_STATUS ($i/60)"
  sleep 2
done

if [ "$PROD_STATUS" != "healthy" ]; then
  err "本番 backend の healthcheck 失敗（最終: $PROD_STATUS）"
  err "候補スロットはまだ動作中です。手動で確認してください。"
  docker compose -f "$COMPOSE_PROD" logs --tail=60 backend >&2
  # 候補スロットは残したままにする（手動ロールバック用）
  exit 1
fi

# -------------------------------------------------------------------
# Phase 3.5: DBマイグレーション（本番スロットで実行）
# -------------------------------------------------------------------
log "Phase 3.5: DBマイグレーション（本番スロット経由）..."
docker exec -e PYTHONPATH=/app "$CONTAINER_BACKEND_PROD" uv run alembic upgrade head

# -------------------------------------------------------------------
# Phase 4: クリーンアップ
# -------------------------------------------------------------------
log "Phase 4: 候補スロットをクリーンアップ..."
docker compose -f "$COMPOSE_CAND" down --remove-orphans

# 不要イメージを削除
# ghcr.io/...：main タグは実行中コンテナが参照中のため prune -a でも削除されない。
# ダングリングイメージ（前回 pull で置き換えられた旧レイヤー）は削除される。
log "Phase 4: 未使用イメージを削除..."
BEFORE=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo "不明")
docker image prune -a -f
AFTER=$(docker system df --format '{{.Size}}' 2>/dev/null | head -1 || echo "不明")
log "  イメージ領域: $BEFORE → $AFTER"

# -------------------------------------------------------------------
# Phase 5: keirin webhook サービスの再起動
# -------------------------------------------------------------------
# 🔴 **これを忘れると承認・取消が無反応になる。**
#    `keirin-webhook.service` は systemd の常駐プロセスで、Docker の外にいる。
#    `git reset --hard` でソースが新しくなっても**古いコードのまま動き続ける**ため、
#    新しく足したルート（/approve・/cancel）だけが 404 になる。
#    2026-08-11 に実際に起きた: 06:51 起動のプロセスが 14:12 更新のコードを読まず、
#    確認画面から承認しても何も起きなかった（memory keirin_webhook_stale_process）。
#
# ⚠️ **`/health` では絶対に検知できない。** 旧コードでも生きているので死活監視は緑のまま。
#    しかも `/submit-race` は旧コードにもあるため「一部だけ動く」状態になり誤診しやすい。
#
# ⚠️ デプロイ全体を失敗にはしない。Web 本体は既に切り替わっており、
#    ここで exit 1 にすると成功したデプロイが失敗として扱われる。
#    失敗したら**目立つ警告**を出して手動再起動を促す。
log "Phase 5: keirin-webhook を再起動..."
if sudo -n systemctl restart keirin-webhook 2>/dev/null; then
  sleep 1
  WEBHOOK_STARTED=$(systemctl show keirin-webhook -p ExecMainStartTimestamp --value 2>/dev/null || echo "不明")
  log "  keirin-webhook 再起動 OK（起動: ${WEBHOOK_STARTED}）"
else
  err "keirin-webhook の再起動に失敗しました。"
  err "  → 承認・取消が古いコードのまま動く可能性があります。"
  err "  → 手動で: sudo systemctl restart keirin-webhook"
fi

docker compose -f "$COMPOSE_PROD" ps
log "=== デプロイ完了: https://galloplab.com/ ==="
