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
# コミット済み差分 + 未コミット変更 + 未追跡ファイル。
# 未追跡を含めるのは意図的: 新規追加した .py / .ts はまだ追跡されていなくても
# lint / 型検査の対象にしなければならない。
CHANGED="$( { git diff --name-only "$MB"...HEAD; git diff --name-only HEAD; \
              git ls-files --others --exclude-standard; } | sort -u )"

# 検査の起動条件は「そのツールが実際に読むファイル」で判定する。
# `^backend/` のような粗い判定にすると、backend/data/ の生成物や
# backend/models/ の学習済みモデルを置いただけで ruff/mypy/pytest が走る。
BACKEND_PAT='^backend/.*\.py$|^backend/(pyproject\.toml|uv\.lock|alembic\.ini)$'
FRONTEND_PAT='^frontend/.*\.(ts|tsx|js|jsx|mjs|cjs)$|^frontend/(package\.json|pnpm-lock\.yaml|tsconfig\.json|next\.config[^/]*)$'

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

# Python ツールの実行方法を解決する。CI と同じ uv を最優先し、
# uv が入っていない開発機では既存の backend/.venv を使う。
# どちらも無ければ「未検査のまま通過」を避けるため失敗させる。
BACKEND_RUNNER=""
if command -v uv >/dev/null 2>&1; then
  BACKEND_RUNNER="uv"
elif [ -x "backend/.venv/bin/ruff" ] && [ -x "backend/.venv/bin/mypy" ]; then
  BACKEND_RUNNER="venv"
fi

py_tool() {   # py_tool <tool> [args...]  — backend/ 配下で実行する
  local tool="$1"; shift
  if [ "$BACKEND_RUNNER" = "uv" ]; then
    ( cd backend && uv run "$tool" "$@" )
  else
    ( cd backend && ".venv/bin/$tool" "$@" )
  fi
}

if echo "$CHANGED" | grep -qE "$BACKEND_PAT"; then
  if [ -n "$BACKEND_RUNNER" ]; then
    step "4/5 Backend (ruff / mypy / pytest) — 実行系: $BACKEND_RUNNER"
    py_tool ruff check .; mark $? "ruff"
    py_tool mypy src/ --ignore-missing-imports; mark $? "mypy"
    if [ "$QUICK" -eq 0 ]; then
      DATABASE_URL="postgresql://test:test@localhost:5432/test" API_KEY=test-key API_ENV=test \
        py_tool pytest tests/ -q --tb=short; mark $? "pytest"
    else
      echo "(--quick: pytest をスキップ)"
    fi
  else
    step "4/5 Backend — 実行系が見つかりません"
    echo "[!] uv も backend/.venv も無いため ruff / mypy / pytest を実行できませんでした。"
    echo "    検査せずに通過させると preflight の意味が無くなるため失敗として扱います。"
    echo "    uv を導入するか、backend/ で仮想環境を作成してください。"
    mark 1 "Python 実行系が無い (Backend 未検査)"
  fi
else
  step "4/5 Backend — 対象ファイルの変更なしのためスキップ"
fi

if echo "$CHANGED" | grep -qE "$FRONTEND_PAT"; then
  step "5/5 Frontend (eslint / tsc)"
  if command -v pnpm >/dev/null 2>&1; then
    ( cd frontend && pnpm lint ); mark $? "eslint"
    ( cd frontend && pnpm exec tsc --noEmit ); mark $? "tsc"
  else
    echo "[!] pnpm が見つからないため eslint / tsc を実行できませんでした。"
    echo "    検査せずに通過させると preflight の意味が無くなるため失敗として扱います。"
    echo "    pnpm を PATH に通してから再実行してください。"
    mark 1 "pnpm が見つからない (Frontend 未検査)"
  fi
else
  step "5/5 Frontend — 対象ファイルの変更なしのためスキップ"
fi

echo
echo "════════════════════════════════════════"
if [ -n "$FAILED" ]; then
  echo "✗ preflight 失敗:"; printf "%s" "$FAILED"
  echo "════════════════════════════════════════"; exit 1
fi
echo "✓ preflight 通過 — コミット / PR して問題ありません"
echo "════════════════════════════════════════"
