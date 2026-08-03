#!/usr/bin/env bash
# ============================================================================
# worktree 管理 — 並列 Claude Code セッションの隔離
#
# 同じフォルダで複数の Claude Code を動かすと、作業ツリー(未コミット変更 +
# インデックス)を共有してしまい破綻する。worktree は「1 リポジトリ + 複数の
# 物理作業フォルダ」を実現し、作業中は完全に隔離される。
#
# 使い方:
#   bash scripts/dev/wt.sh new keirin combo-priority        # feat/keirin-combo-priority
#   bash scripts/dev/wt.sh new chihou v14-features fix      # fix/chihou-v14-features
#   bash scripts/dev/wt.sh list
#   bash scripts/dev/wt.sh sync keirin/combo-priority       # origin/main を取り込む
#   bash scripts/dev/wt.sh rm   keirin/combo-priority
#
# worktree の置き場所: 既定で <リポジトリの親>/kiseki-wt/<柱>/<トピック>
#   KISEKI_WT_ROOT で変更可。リポジトリ外に置くのは ruff/mypy/pytest/next が
#   worktree 内を二重スキャンするのを防ぐため。
# ============================================================================
set -uo pipefail

MAIN_WT="$(git worktree list --porcelain | head -1 | sed 's/^worktree //')"
WT_ROOT="${KISEKI_WT_ROOT:-$(dirname "$MAIN_WT")/kiseki-wt}"
VALID_PILLARS="keirin chihou jra shared"

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

cmd_new() {
  local pillar="${1:-}" topic="${2:-}" type="${3:-feat}"
  [ -z "$pillar" ] || [ -z "$topic" ] && usage
  if ! echo "$VALID_PILLARS" | grep -qw "$pillar"; then
    echo "ERROR: 柱は次のいずれか: $VALID_PILLARS" >&2; exit 1
  fi
  local branch="${type}/${pillar}-${topic}"
  local path="${WT_ROOT}/${pillar}/${topic}"

  if [ -e "$path" ]; then echo "ERROR: 既に存在します: $path" >&2; exit 1; fi

  echo "→ origin/main を取得中..."
  git fetch origin main --quiet 2>/dev/null || echo "  (fetch 失敗: オフラインのためローカルの origin/main を使用)"

  local base="origin/main"
  git rev-parse --verify "$base" >/dev/null 2>&1 || base="main"

  mkdir -p "$(dirname "$path")"
  git worktree add "$path" -b "$branch" "$base" || exit 1

  # 権限allowlistを引き継ぐ (未追跡のため worktree には自動で入らない)
  if [ -f "$MAIN_WT/.claude/settings.local.json" ]; then
    mkdir -p "$path/.claude"
    cp "$MAIN_WT/.claude/settings.local.json" "$path/.claude/settings.local.json"
    echo "  .claude/settings.local.json を引き継ぎました"
  fi
  # 環境変数も引き継ぐ (.env は gitignore のため worktree に入らない)
  for envf in .env .env.sekito; do
    [ -f "$MAIN_WT/$envf" ] && cp "$MAIN_WT/$envf" "$path/$envf" && echo "  $envf を引き継ぎました"
  done

  cat <<EOS

=== worktree を作成しました ===
  ブランチ: $branch
  パス:     $path
  柱:       $pillar

次の手順:
  cd "$path"
  claude          # ← このフォルダ専用の Claude Code を起動
  # 作業後:
  bash scripts/dev/preflight.sh     # コミット前チェック
EOS
}

cmd_list() {
  echo "=== worktree 一覧 ==="
  git worktree list
  echo
  echo "=== 各ブランチの状態 ==="
  git worktree list --porcelain | awk '/^worktree /{p=$2} /^branch /{sub("refs/heads/","",$2); print p"\t"$2}' \
  | while IFS=$'\t' read -r p b; do
      local_ahead="$(git rev-list --count "origin/main..$b" 2>/dev/null || echo '?')"
      dirty="$(git -C "$p" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
      printf "  %-28s ahead:%-4s 未コミット:%-4s %s\n" "$b" "$local_ahead" "$dirty" "$p"
    done
}

cmd_sync() {
  local name="${1:-}"; [ -z "$name" ] && usage
  local path="${WT_ROOT}/${name}"
  [ -d "$path" ] || { echo "ERROR: worktree がありません: $path" >&2; exit 1; }
  git fetch origin main --quiet 2>/dev/null || true
  echo "→ $name を origin/main へ rebase します"
  git -C "$path" rebase origin/main || {
    echo "  [!] rebase で衝突しました。$path で解決してください:"
    echo "      git -C '$path' status"
    echo "      解決後: git -C '$path' rebase --continue"
    exit 1; }
  echo "  OK: 最新の origin/main を取り込みました"
}

cmd_rm() {
  local name="${1:-}"; [ -z "$name" ] && usage
  local path="${WT_ROOT}/${name}"
  git worktree remove "$path" && echo "削除しました: $path" || {
    echo "未コミットの変更が残っている可能性があります。強制削除は:" >&2
    echo "  git worktree remove --force '$path'" >&2; exit 1; }
}

case "${1:-}" in
  new)  shift; cmd_new  "$@" ;;
  list) shift; cmd_list "$@" ;;
  sync) shift; cmd_sync "$@" ;;
  rm)   shift; cmd_rm   "$@" ;;
  *) usage ;;
esac
