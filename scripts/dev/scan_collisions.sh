#!/usr/bin/env bash
# ============================================================================
# ブランチ間コンフリクト事前検知 (conflict-scout の実体)
#
# 「マージして初めて衝突に気づく」のを避けるため、作業中の他ブランチと
# 同じファイルを触っていないかを *事前に* 洗い出す。
#
# 使い方:
#   bash scripts/dev/scan_collisions.sh            # 現ブランチ vs 他の全ブランチ
#   bash scripts/dev/scan_collisions.sh --all      # 全ブランチ総当たり (PM 用)
# ============================================================================
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT" || exit 1
source "$REPO_ROOT/scripts/dev/pillars.sh"

BASE="origin/main"
git rev-parse --verify "$BASE" >/dev/null 2>&1 || BASE="main"

files_of() {
  local br="$1" mb
  mb="$(git merge-base "$br" "$BASE" 2>/dev/null)" || return 0
  git diff --name-only "$mb".."$br" 2>/dev/null
}

active_branches() {
  git for-each-ref --format='%(refname:short)' refs/heads \
    | grep -vE '^(main|master)$'
}

report_pair() {
  local a="$1" b="$2" overlap
  overlap="$(comm -12 <(files_of "$a" | sort -u) <(files_of "$b" | sort -u))"
  [ -z "$overlap" ] && return 0
  echo
  echo "  [衝突リスク] $a  <->  $b"
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    printf "      %-8s %s\n" "[$(pillar_of "$f")]" "$f"
  done <<< "$overlap"
}

if [ "${1:-}" = "--all" ]; then
  echo "=== 全ブランチ総当たり衝突スキャン (base: $BASE) ==="
  mapfile -t BRS < <(active_branches)
  found=0
  for ((i=0; i<${#BRS[@]}; i++)); do
    for ((j=i+1; j<${#BRS[@]}; j++)); do
      out="$(report_pair "${BRS[i]}" "${BRS[j]}")"
      [ -n "$out" ] && { echo "$out"; found=1; }
    done
  done
  [ "$found" -eq 0 ] && echo "  重複ファイルなし。全ブランチは安全に並列マージできます。"
else
  CUR="$(git rev-parse --abbrev-ref HEAD)"
  echo "=== '$CUR' と他ブランチの衝突スキャン (base: $BASE) ==="
  found=0
  while IFS= read -r br; do
    [ "$br" = "$CUR" ] && continue
    out="$(report_pair "$CUR" "$br")"
    [ -n "$out" ] && { echo "$out"; found=1; }
  done < <(active_branches)
  [ "$found" -eq 0 ] && echo "  重複ファイルなし。安全にマージできます。"
fi
