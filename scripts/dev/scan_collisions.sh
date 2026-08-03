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
#
# 本スクリプトは *情報提供* であり合否判定ではない。同じファイルを触っている
# こと自体は違反ではなく (同一ファイルの別箇所なら綺麗にマージされる)、
# 「注意して見るべき組」を人間に示すのが目的。したがって衝突を検出しても
# 終了コードは常に 0 を返す。preflight のブロック条件にしてはいけない。
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
  # macOS 標準の bash 3.2 には mapfile が無いため read ループで配列を作る
  BRS=()
  while IFS= read -r _br; do
    [ -n "$_br" ] && BRS+=("$_br")
  done < <(active_branches)
  found=0
  [ "${#BRS[@]}" -eq 0 ] && { echo "  比較対象のブランチがありません。"; exit 0; }
  for ((i=0; i<${#BRS[@]}; i++)); do
    for ((j=i+1; j<${#BRS[@]}; j++)); do
      out="$(report_pair "${BRS[i]}" "${BRS[j]}")"
      [ -n "$out" ] && { echo "$out"; found=1; }
    done
  done
  [ "$found" -eq 0 ] && echo "  重複ファイルなし。全ブランチは安全に並列マージできます。"
  exit 0
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
  exit 0
fi
