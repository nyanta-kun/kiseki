#!/usr/bin/env bash
# ============================================================================
# コミット / PR 前の総合チェック
#
# CI で落ちる前に手元で全部潰す。変更のあった領域だけを検査するので速い。
#
# 使い方:
#   bash scripts/dev/preflight.sh            # 通常 (lint + 型 + テスト)
#   bash scripts/dev/preflight.sh --quick    # テストを省略 (lint + 型のみ)
# ============================================================================
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT" || exit 1

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

BASE="origin/main"
git rev-parse --verify "$BASE" >/dev/null 2>&1 || BASE="main"
MB="$(git merge-base HEAD "$BASE" 2>/dev/null || echo "$BASE")"
CHANGED="$( { git diff --name-only "$MB"...HEAD; git diff --name-only HEAD; \
              git ls-files --others --exclude-standard; } | sort -u )"

FAILED=""
step() { echo; echo "────────────────────────────────────────"; echo "▶ $1"; echo "────────────────────────────────────────"; }
mark() { [ "$1" -ne 0 ] && FAILED="${FAILED}  - $2"$'\n'; }

step "1/5 Alembic マイグレーション整合"
bash scripts/dev/check_migrations.sh; mark $? "check_migrations"

step "2/5 柱(pillar)所属チェック"
bash scripts/dev/check_ownership.sh; mark $? "check_ownership"

step "3/5 他ブランチとの衝突スキャン (情報提供のみ・ブロックしない)"
# 同じファイルを触っていること自体は違反ではない。ここで落とすと、作業中の
# ブランチや削除し忘れたローカルブランチがあるだけで preflight が通らなくなる。
bash scripts/dev/scan_collisions.sh || true

if echo "$CHANGED" | grep -q '^backend/'; then
  step "4/5 Backend (ruff / mypy / pytest)"
  if command -v uv >/dev/null 2>&1; then
    ( cd backend && uv run ruff check . ); mark $? "ruff"
    ( cd backend && uv run mypy src/ --ignore-missing-imports ); mark $? "mypy"
    if [ "$QUICK" -eq 0 ]; then
      ( cd backend && DATABASE_URL="postgresql://test:test@localhost:5432/test" API_KEY=test-key API_ENV=test \
        uv run pytest tests/ -q --tb=short ); mark $? "pytest"
    else
      echo "(--quick: pytest をスキップ)"
    fi
  else
    echo "[skip] uv が見つかりません"
  fi
else
  step "4/5 Backend — 変更なしのためスキップ"
fi

if echo "$CHANGED" | grep -q '^frontend/'; then
  step "5/5 Frontend (eslint / tsc)"
  if command -v pnpm >/dev/null 2>&1; then
    ( cd frontend && pnpm lint ); mark $? "eslint"
    ( cd frontend && pnpm exec tsc --noEmit ); mark $? "tsc"
  else
    echo "[skip] pnpm が見つかりません"
  fi
else
  step "5/5 Frontend — 変更なしのためスキップ"
fi

echo
echo "════════════════════════════════════════"
if [ -n "$FAILED" ]; then
  echo "✗ preflight 失敗:"; printf "%s" "$FAILED"
  echo "════════════════════════════════════════"; exit 1
fi
echo "✓ preflight 通過 — コミット / PR して問題ありません"
echo "════════════════════════════════════════"
