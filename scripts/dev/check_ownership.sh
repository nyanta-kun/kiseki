#!/usr/bin/env bash
# ============================================================================
# 変更ファイルの「柱」所属チェック
#
# 並列開発でコンフリクトが起きる最大の原因は、複数ブランチが同じファイル
# (特に shared ゾーン) を同時に触ること。作業前・PR 前にこれを可視化する。
#
# 使い方:
#   bash scripts/dev/check_ownership.sh              # origin/main との差分を判定
#   bash scripts/dev/check_ownership.sh main         # 比較先を指定
#   OWNERSHIP_STRICT=1 bash scripts/dev/check_ownership.sh   # 複数柱混在を失敗扱い
#
# 終了コード: 0=OK / 1=STRICT時の違反
# ============================================================================
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT" || exit 1
# shellcheck source=scripts/dev/pillars.sh
source "$REPO_ROOT/scripts/dev/pillars.sh"

BASE_REF="${1:-origin/main}"
git rev-parse --verify "$BASE_REF" >/dev/null 2>&1 || BASE_REF="main"

MERGE_BASE="$(git merge-base HEAD "$BASE_REF" 2>/dev/null)" || MERGE_BASE="$BASE_REF"

# コミット済み差分 + 未コミット変更 + 未追跡ファイルをすべて対象にする
CHANGED="$( { git diff --name-only "$MERGE_BASE"...HEAD;
              git diff --name-only HEAD;
              git ls-files --others --exclude-standard; } | sort -u | grep -v '^$' )"

if [ -z "$CHANGED" ]; then
  echo "変更ファイルはありません (base: $BASE_REF)"
  exit 0
fi

echo "=== 変更ファイルの柱判定 (base: $BASE_REF) ==="
SHARED_FILES=""
while IFS= read -r f; do
  pl="$(pillar_of "$f")"
  printf "  %-8s %s\n" "[$pl]" "$f"
  [ "$pl" = "shared" ] && SHARED_FILES="${SHARED_FILES}${f}"$'\n'
done <<< "$CHANGED"

PILLARS="$(echo "$CHANGED" | pillars_of_files | grep -Ev '^(other)$' || true)"
CODE_PILLARS="$(echo "$PILLARS" | grep -Ev '^(shared)$' || true)"
N_PILLARS="$(echo "$CODE_PILLARS" | grep -c . || true)"

echo
echo "=== サマリ ==="
echo "  関与する柱: $(echo "$CODE_PILLARS" | tr '\n' ' ')"

RC=0

if [ -n "$SHARED_FILES" ]; then
  echo
  echo "  [!] shared ゾーンを変更しています。全柱に波及します:"
  echo "$SHARED_FILES" | grep -v '^$' | sed 's/^/      - /'
  echo "      → 単独 PR にして最優先でマージし、他ブランチは直後に rebase してください。"
  echo "$SHARED_FILES" | grep -q '^backend/alembic/' && {
    echo "      → alembic を含みます。必ず bash scripts/dev/check_migrations.sh を実行してください。"; }
fi

if [ "$N_PILLARS" -gt 1 ]; then
  echo
  echo "  [!] 1 ブランチで複数の柱を変更しています ($N_PILLARS 柱)。"
  echo "      → 柱ごとにブランチを分割すると並列マージが安全になります。"
  [ "${OWNERSHIP_STRICT:-0}" = "1" ] && RC=1
fi

[ "$RC" -eq 0 ] && echo "  判定: OK"
exit "$RC"
