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
    backend/src/realtime/*) echo shared ;;
    backend/src/__init__.py|backend/src/bet_types.py) echo shared ;;
    backend/pyproject.toml|backend/Dockerfile|backend/alembic.ini) echo shared ;;
    backend/.dockerignore|frontend/.dockerignore) echo shared ;;
    .env|.env.*) echo shared ;;   # 環境変数テンプレート (実 .env は追跡しない)
    frontend/package.json|frontend/pnpm-lock.yaml|frontend/next.config*|frontend/tsconfig.json) echo shared ;;
    frontend/auth.*|frontend/middleware.*|frontend/Dockerfile) echo shared ;;
    # フロントの横断部分: 認証・レイアウト・全柱共通ナビ・API 型定義。
    # ここは3柱すべてが読むので、触ると全画面に波及する。
    frontend/src/auth.*|frontend/src/proxy.*|frontend/src/types/*) echo shared ;;
    frontend/src/app/auth/*|frontend/src/app/api/auth/*|frontend/src/app/actions/*) echo shared ;;
    frontend/src/app/layout.tsx|frontend/src/app/globals.css|frontend/src/app/page.tsx) echo shared ;;
    frontend/src/lib/api.ts|frontend/src/lib/utils.ts|frontend/src/hooks/useWebSocket.ts) echo shared ;;
    frontend/src/components/AppNav.tsx|frontend/src/components/BottomNav.tsx) echo shared ;;
    frontend/src/components/HamburgerMenu.tsx|frontend/src/components/SiteHeader.tsx) echo shared ;;
    frontend/src/components/Footer.tsx|frontend/src/components/PaywallGate.tsx) echo shared ;;
    frontend/src/components/LogoutButton.tsx) echo shared ;;
    .github/*|docker-compose*|Makefile|.gitignore|.pre-commit-config.yaml) echo shared ;;
    # AGENTS.md は CLAUDE.md の写し (test_agents_md_mirrors_claude_md.py が同一性を固定)。
    # 片方だけ shared 扱いにすると、対で編集する変更が警告をすり抜ける。
    CLAUDE.md|AGENTS.md|scripts/dev/*|.claude/*|.agents/*|.codex/*) echo shared ;;
    .planning/*) echo shared ;;   # ルート直下の計画文書 (backend/.planning/* は柱判定に委ねる)
    # 全柱に効くインフラ運用スクリプト。デプロイは3柱すべてを同時に入れ替え、
    # バックアップ / スキーマ操作は DB 全体 (keiba / sekito / chihou) を触る。
    scripts/deploy-*|scripts/backup*|scripts/launchagents/*) echo shared ;;
    scripts/setup_schema.py|scripts/clear_imported_data.py) echo shared ;;

    # ---- 柱ごと: キーワードで判定 (shared 判定の後に評価すること) ----
    *chihou*|*Chihou*|*CHIHOU*) echo chihou ;;
    *keirin*|*Keirin*|*KEIRIN*|*yoso*|*Yoso*) echo keirin ;;

    # ---- jra: 残りのバックエンド中核 + JV-Link 系 ----
    windows-agent/*) echo jra ;;
    backend/src/importers/*|backend/src/indices/*|backend/src/api/*) echo jra ;;
    backend/src/services/*|backend/models/*|backend/tests/*) echo jra ;;
    scripts/jra_*|scripts/daily_*|scripts/odds_*|scripts/dm_*|scripts/realtime_*) echo jra ;;
    # 🔴 ここから下は 2026-09-01 追加。それまで backend/scripts (118件) と
    #    frontend (104件) が丸ごと other に落ちており、check_ownership.sh が
    #    other を集計から除外するため **shared 警告も複数柱警告も一切効いていなかった**。
    #    柱ごとのキーワード判定より後ろに置くこと（chihou_*/keirin_* を先に拾わせる）。
    backend/scripts/*|backend/src/*) echo jra ;;
    frontend/*) echo jra ;;
    scripts/*) echo jra ;;

    # other = コード以外 (docs / inputs / 引き継ぎメモ / 生成物)。
    # 衝突の原因にならないので柱には割り当てないが、check_ownership.sh は
    # 「未分類」として必ず件数を表示する。黙って捨てないこと。
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
