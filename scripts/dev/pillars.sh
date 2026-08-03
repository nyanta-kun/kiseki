#!/usr/bin/env bash
# ============================================================================
# 柱(pillar)判定の唯一の情報源 (Single Source of Truth)
#
# kiseki は「競輪 / 中央競馬 / 地方競馬」の3本柱が1リポジトリに同居する。
# 並列開発でコンフリクトを避ける最大の鍵は「どのファイルが誰のものか」を
# 機械的に判定できること。判定ロジックはこのファイルにだけ書く。
#
# 使い方:
#   source scripts/dev/pillars.sh
#   pillar_of backend/src/api/keirin_router.py   # -> keirin
#
# 出力: shared | keirin | chihou | jra | other
#   shared = 全柱に影響する高衝突ゾーン。触る場合は PM 調整 + 単独 PR 推奨。
# ============================================================================

pillar_of() {
  case "$1" in
    # ---- shared: 触ると全柱に波及する。並列で触ってはいけない ----
    backend/alembic/*) echo shared ;;
    backend/src/db/models.py|backend/src/db/session.py|backend/src/db/__init__.py) echo shared ;;
    backend/src/main.py|backend/src/config.py) echo shared ;;
    backend/src/utils/*) echo shared ;;
    backend/src/indices/base.py|backend/src/indices/composite.py) echo shared ;;
    backend/src/betting/*) echo shared ;;
    backend/src/api/access.py|backend/src/api/users.py|backend/src/api/ws_manager.py) echo shared ;;
    backend/pyproject.toml|backend/Dockerfile|backend/alembic.ini) echo shared ;;
    frontend/package.json|frontend/pnpm-lock.yaml|frontend/next.config*|frontend/tsconfig.json) echo shared ;;
    frontend/auth.*|frontend/middleware.*|frontend/Dockerfile) echo shared ;;
    .github/*|docker-compose*|Makefile|.gitignore|.pre-commit-config.yaml) echo shared ;;
    CLAUDE.md|scripts/dev/*|.claude/*) echo shared ;;
    .planning/*) echo shared ;;   # ルート直下の計画文書 (backend/.planning/* は柱判定に委ねる)

    # ---- 柱ごと: キーワードで判定 (shared 判定の後に評価すること) ----
    *chihou*|*Chihou*|*CHIHOU*) echo chihou ;;
    *keirin*|*Keirin*|*KEIRIN*|*yoso*|*Yoso*) echo keirin ;;

    # ---- jra: 残りのバックエンド中核 + JV-Link 系 ----
    windows-agent/*) echo jra ;;
    backend/src/importers/*|backend/src/indices/*|backend/src/api/*) echo jra ;;
    backend/src/services/*|backend/models/*|backend/tests/*) echo jra ;;
    scripts/jra_*|scripts/daily_*|scripts/odds_*|scripts/dm_*|scripts/realtime_*) echo jra ;;

    *) echo other ;;
  esac
}

# 変更ファイル一覧を受け取り、含まれる柱をユニークに列挙する
pillars_of_files() {
  local f
  while IFS= read -r f; do
    [ -n "$f" ] && pillar_of "$f"
  done | sort -u
}
