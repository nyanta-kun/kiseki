#!/usr/bin/env bash
# ============================================================================
# 並列開発ダッシュボード — 全体像を 1 画面で把握する
#
# 使い方: bash scripts/dev/pd_status.sh
# ============================================================================
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT" || exit 1
source "$REPO_ROOT/scripts/dev/pillars.sh"

BASE="origin/main"
git rev-parse --verify "$BASE" >/dev/null 2>&1 || BASE="main"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  kiseki 並列開発ステータス                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"

echo
echo "■ trunk (main)"
git log --oneline -1 "$BASE" | sed 's/^/    /'
MAIN_WT="$(git worktree list --porcelain | head -1 | sed 's/^worktree //')"
DIRTY="$(git -C "$MAIN_WT" status --porcelain | wc -l | tr -d ' ')"
if [ "$DIRTY" -gt 0 ]; then
  echo "    [!] main の作業ツリーに未コミット $DIRTY 件 — trunk は常にクリーンに保つのが原則です"
else
  echo "    クリーン"
fi

echo
echo "■ worktree"
git worktree list | sed 's/^/    /'

echo
echo "■ 稼働中ブランチ (origin/main から進んでいるもの)"
printf "    %-42s %-6s %-24s\n" "ブランチ" "ahead" "柱"
git for-each-ref --format='%(refname:short)' refs/heads | grep -vE '^(main|master)$' | while read -r br; do
  mb="$(git merge-base "$br" "$BASE" 2>/dev/null)" || continue
  ahead="$(git rev-list --count "$mb..$br" 2>/dev/null || echo 0)"
  [ "$ahead" -eq 0 ] && continue
  pl="$(git diff --name-only "$mb".."$br" | pillars_of_files | tr '\n' ' ')"
  printf "    %-42s %-6s %-24s\n" "$br" "$ahead" "$pl"
done

echo
echo "■ マージ済み / 削除可能なローカルブランチ"
git for-each-ref --format='%(refname:short)' refs/heads | grep -vE '^(main|master)$' | while read -r br; do
  mb="$(git merge-base "$br" "$BASE" 2>/dev/null)" || continue
  [ "$(git rev-list --count "$mb..$br" 2>/dev/null || echo 0)" -eq 0 ] && echo "    $br"
done

echo
echo "■ Alembic"
bash scripts/dev/check_migrations.sh 2>&1 | sed 's/^/    /'

echo
echo "■ 次にすべきこと"
echo "    新規作業:   bash scripts/dev/wt.sh new <柱> <トピック>"
echo "    統合計画:   bash scripts/dev/integrate.sh --plan"
echo "    衝突確認:   bash scripts/dev/scan_collisions.sh --all"
