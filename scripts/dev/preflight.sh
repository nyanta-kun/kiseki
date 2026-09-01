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
# 🔴 keirin は 2026-08-10 に統合されたが preflight に入っておらず、**CI だけが
#    落とす**状態だった（2026-08-12 に実際に往復した: bet_detail の形を変えて
#    preflight は通ったのに CI の Keirin ジョブで既存テストが落ちた）。
#    デプロイ経路は VPS のリポジトリを直接使うので、ここを見ないと
#    「手元で通ったのに本番の入稿だけ壊れる」が起こりうる。
KEIRIN_PAT='^keirin/.*\.py$|^keirin/(requirements[^/]*\.txt|pytest\.ini|pyproject\.toml)$'
WINAGENT_PAT='^windows-agent/.*\.(py|vbs|ps1|bat)$'

FAILED=""
step() { echo; echo "────────────────────────────────────────"; echo "▶ $1"; echo "────────────────────────────────────────"; }
mark() { [ "$1" -ne 0 ] && FAILED="${FAILED}  - $2"$'\n'; }

step "1/8 Alembic マイグレーション整合"
bash scripts/dev/check_migrations.sh; mark $? "check_migrations"

step "2/8 柱(pillar)所属チェック"
bash scripts/dev/check_ownership.sh; mark $? "check_ownership"

step "3/8 他ブランチとの衝突スキャン (情報提供のみ・ブロックしない)"
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

winagent_pytest() {   # winagent_pytest <pytest 引数...> — windows-agent/ 配下で実行する
  # ⚠️ cwd は必ず windows-agent にすること。link_common のロガーは **cwd 直下**に
  #    jvlink_agent.log / umaconn_agent.log を掘るため、backend/ から実行すると
  #    backend/umaconn_agent.log が毎回ゴミとして残る（backend 側に *.log の
  #    ignore が無い。windows-agent/.gitignore には *.log がある）。
  if [ "$BACKEND_RUNNER" = "uv" ]; then
    ( cd windows-agent && uv run --project ../backend python -m pytest "$@" )
  else
    ( cd windows-agent && ../backend/.venv/bin/python -m pytest "$@" )
  fi
}

# ---------------------------------------------------------------------------
# Windows スクリプトの改行コード検査は **変更の有無に関わらず常時実行する**。
#
# 🔴 これは「触ったファイルが正しいか」ではなく「作業ツリーが健全か」の検査。
#    .gitattributes の eol=crlf は **チェックアウト時にしか走らない**ため、
#    フィルタ導入より前に作られた作業ツリー / clone はファイルが LF のまま残る。
#    リポジトリ側の blob は正規化済み(LF)で `git diff` は差分を出さず、
#    **新規 clone では必ず CRLF になるので CI では絶対に再現しない**
#    (2026-09-01 実測: fresh clone 19件すべて CRLF・CI は常に緑)。
#    一方 Windows は Z:\ マウント経由で**この作業ツリーの実体**を読むため、
#    壊れているのは配備物だけという状態になる。変更検知では捕まえられない。
#
#    実害: 2026-09-01 時点で 4 ファイル (run_jvlink_race.vbs / run_jvlink_toku.vbs /
#    start_agent.bat / register_start_jvlinkagent_task.ps1) が LF のまま残っていた。
#    復旧は `rm -f windows-agent/*.vbs *.ps1 *.bat && git checkout -- windows-agent/`。
#
#    検査の実体は windows-agent/tests/test_windows_script_encoding.py に一本化する
#    (ここに同じ判定を書き写すと正本が2つになる)。実測 0.05 秒なので常時実行して問題ない。
# ---------------------------------------------------------------------------
step "4/8 Windows スクリプトの改行コード (作業ツリー健全性・常時実行)"
if [ -n "$BACKEND_RUNNER" ]; then
  winagent_pytest tests/test_windows_script_encoding.py -q --no-header
  mark $? "windows script eol (CRLF)"
else
  echo "[!] uv も backend/.venv も無いため改行コード検査を実行できませんでした。"
  echo "    この検査は作業ツリーの配備物そのものを見るもので、CI では代替できません。"
  mark 1 "Python 実行系が無い (Windows スクリプト改行コード未検査)"
fi

if echo "$CHANGED" | grep -qE "$BACKEND_PAT"; then
  if [ -n "$BACKEND_RUNNER" ]; then
    step "5/8 Backend (ruff / mypy / pytest) — 実行系: $BACKEND_RUNNER"
    py_tool ruff check .; mark $? "ruff"
    py_tool mypy src/ --ignore-missing-imports; mark $? "mypy"
    if [ "$QUICK" -eq 0 ]; then
      DATABASE_URL="postgresql://test:test@localhost:5432/test" API_KEY=test-key API_ENV=test \
        py_tool pytest tests/ -q --tb=short; mark $? "pytest"
    else
      echo "(--quick: pytest をスキップ)"
    fi
  else
    step "5/8 Backend — 実行系が見つかりません"
    echo "[!] uv も backend/.venv も無いため ruff / mypy / pytest を実行できませんでした。"
    echo "    検査せずに通過させると preflight の意味が無くなるため失敗として扱います。"
    echo "    uv を導入するか、backend/ で仮想環境を作成してください。"
    mark 1 "Python 実行系が無い (Backend 未検査)"
  fi
else
  step "5/8 Backend — 対象ファイルの変更なしのためスキップ"
fi

if echo "$CHANGED" | grep -qE "$FRONTEND_PAT"; then
  step "6/8 Frontend (eslint / tsc)"
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
  step "6/8 Frontend — 対象ファイルの変更なしのためスキップ"
fi

if echo "$CHANGED" | grep -qE "$KEIRIN_PAT"; then
  step "7/8 Keirin (pytest)"
  # keirin は自前の venv を持つ（backend とは別。LightGBM 等の重い依存があり
  # backend の venv では動かない）。無ければ **skip ではなく失敗**にする
  # ——「未検査のまま通過」は preflight の意味を無くす。
  if [ -x "keirin/.venv/bin/python" ]; then
    ( cd keirin && PYTHONPATH=. .venv/bin/python -m pytest tests/ -q ); mark $? "keirin pytest"
  else
    echo "[!] keirin/.venv が無いため keirin のテストを実行できませんでした。"
    echo "    検査せずに通過させると preflight の意味が無くなるため失敗として扱います。"
    echo "    keirin/ で仮想環境を作るか、VPS 上で実行してください。"
    mark 1 "keirin/.venv が無い (Keirin 未検査)"
  fi
else
  step "7/8 Keirin — 対象ファイルの変更なしのためスキップ"
fi

if echo "$CHANGED" | grep -qE "$WINAGENT_PAT"; then
  step "8/8 Windows Agent (pytest)"
  # windows-agent は backend の venv で動く（pywin32 が要る箇所は各テストが skip する）。
  # ⚠️ 対象は tests/ だけに絞ること。windows-agent 直下の test_*.py は実体が
  #    JV-Link / UmaConn の手動プローブで、import 時に sys.exit(1) を呼ぶものがあり
  #    pytest の収集ごとクラッシュする。
  if [ -n "$BACKEND_RUNNER" ]; then
    winagent_pytest tests/ -q --tb=short; mark $? "windows-agent pytest"
  else
    echo "[!] uv も backend/.venv も無いため windows-agent のテストを実行できませんでした。"
    echo "    検査せずに通過させると preflight の意味が無くなるため失敗として扱います。"
    mark 1 "Python 実行系が無い (Windows Agent 未検査)"
  fi
else
  step "8/8 Windows Agent — 対象ファイルの変更なしのためスキップ"
fi

echo
echo "════════════════════════════════════════"
if [ -n "$FAILED" ]; then
  echo "✗ preflight 失敗:"; printf "%s" "$FAILED"
  echo "════════════════════════════════════════"; exit 1
fi
echo "✓ preflight 通過 — コミット / PR して問題ありません"
echo "════════════════════════════════════════"
