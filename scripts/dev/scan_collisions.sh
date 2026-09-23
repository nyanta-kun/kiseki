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
  local br mb
  br="$(resolve_ref "$1")"
  mb="$(git merge-base "$br" "$BASE" 2>/dev/null)" || return 0
  git diff --name-only "$mb".."$br" 2>/dev/null
}

# 🔴 **origin のブランチも見ること**（2026-09-23 修正）。
#    worktree 構成では全ブランチが1リポジトリの refs/heads にあったが、柱ごとに
#    clone を分けた（~/GitHub/kiseki-dev/）結果、**他フォルダの作業ブランチは
#    origin にしか無くなった**。refs/heads だけを見ていた旧実装は、構成変更の時点で
#    「他の clone との衝突」を1件も検出できなくなっていた（空振りするだけで
#    エラーにならないので気づけない）。
#
#    ローカルに同名ブランチがある場合は origin 側を落とす（同じ作業を2回出さない）。
active_branches() {
  {
    git for-each-ref --format='%(refname:short)' refs/heads
    # ⚠️ %(refname:short) は refs/remotes/origin/HEAD を **origin** と略すので、
    #    短縮名で HEAD を弾こうとしても引っかからない。フル名で落とすこと。
    git for-each-ref --format='%(refname)' refs/remotes/origin \
      | grep -vE '/HEAD$' \
      | sed 's|^refs/remotes/origin/||'
  } | grep -vE '^(main|master)$' | sort -u
}

#: ブランチ名を実在する ref へ解決する（ローカル優先・無ければ origin/）。
resolve_ref() {
  git rev-parse --verify -q "$1" >/dev/null && { echo "$1"; return; }
  echo "origin/$1"
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
