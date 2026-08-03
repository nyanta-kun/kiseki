#!/usr/bin/env bash
# ============================================================================
# 統合PM: 複数ブランチの順次マージ
#
# 並列でマージすると統合不整合の発見が遅れる。1本ずつ「マージ → 検証 →
# 残りを rebase」を繰り返すのが最も安全で、原因の切り分けも容易。
#
# 使い方:
#   bash scripts/dev/integrate.sh --plan                      # 順序提案 + 衝突予測のみ
#   bash scripts/dev/integrate.sh --dry-run br1 br2 br3       # 実際にマージせず衝突判定
#   bash scripts/dev/integrate.sh br1 br2 br3                 # 順次マージを実行
#
# 原則:
#   - shared ゾーンを含むブランチを最優先でマージする (土台を先に land)
#   - 1本マージするごとに検証し、失敗したらそこで停止する
#   - マージ後は残りのブランチを rebase してから次へ進む
# ============================================================================
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT" || exit 1
source "$REPO_ROOT/scripts/dev/pillars.sh"

BASE="origin/main"
git rev-parse --verify "$BASE" >/dev/null 2>&1 || BASE="main"

branch_pillars() {
  local br="$1" mb
  mb="$(git merge-base "$br" "$BASE" 2>/dev/null)" || return
  git diff --name-only "$mb".."$br" | pillars_of_files | tr '\n' ' '
}

# shared を含むブランチを先頭に、以降は変更ファイル数の少ない順
plan_order() {
  local br
  for br in "$@"; do
    local pl n prio
    pl="$(branch_pillars "$br")"
    n="$(git diff --name-only "$(git merge-base "$br" "$BASE")".."$br" 2>/dev/null | wc -l | tr -d ' ')"
    prio=1; echo "$pl" | grep -qw shared && prio=0
    printf "%s\t%06d\t%s\t%s\n" "$prio" "$n" "$br" "$pl"
  done | sort | awk -F'\t' '{printf "%s\t%s\n", $3, $4}'
}

if [ "${1:-}" = "--plan" ]; then
  shift
  # macOS 標準の bash 3.2 には mapfile が無いため read ループで配列を作る
  BRS=()
  while IFS= read -r _br; do
    [ -n "$_br" ] && BRS+=("$_br")
  done < <(git for-each-ref --format='%(refname:short)' refs/heads | grep -vE '^(main|master)$')
  [ $# -gt 0 ] && BRS=("$@")
  if [ "${#BRS[@]}" -eq 0 ]; then
    echo "統合対象のブランチがありません (base: $BASE)"
    exit 0
  fi
  echo "=== 推奨マージ順序 (base: $BASE) ==="
  i=1
  while IFS=$'\t' read -r br pl; do
    printf "  %2d. %-38s 柱: %s\n" "$i" "$br" "$pl"
    i=$((i+1))
  done < <(plan_order "${BRS[@]}")
  echo
  echo "=== 衝突予測 ==="
  bash scripts/dev/scan_collisions.sh --all
  exit 0
fi

DRY=0
[ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
[ $# -eq 0 ] && { echo "ブランチを指定してください。順序提案は --plan"; exit 1; }

CUR="$(git rev-parse --abbrev-ref HEAD)"
echo "統合先: $CUR   モード: $([ "$DRY" -eq 1 ] && echo DRY-RUN || echo 実マージ)"
[ -n "$(git status --porcelain)" ] && { echo "ERROR: 作業ツリーが汚れています。コミットか stash をしてください。" >&2; exit 1; }

for br in "$@"; do
  echo
  echo "════════════════════════════════════════"
  echo "▶ $br  (柱: $(branch_pillars "$br"))"
  echo "════════════════════════════════════════"

  # マージ前の HEAD を記録する。ロールバックは必ずこの SHA へ戻す。
  # HEAD~1 で戻してはいけない: 「Already up to date」等でマージコミットが
  # 作られなかった場合、HEAD~1 は統合先の既存コミットを指すため、
  # reset --hard が無関係な作業を破壊する。
  BEFORE="$(git rev-parse HEAD)"

  if ! git merge --no-commit --no-ff "$br" >/dev/null 2>&1; then
    echo "  ✗ 衝突を検出しました。競合ファイル:"
    git diff --name-only --diff-filter=U | sed 's/^/      /'
    git merge --abort 2>/dev/null || git reset --hard "$BEFORE" >/dev/null
    echo "  → $br 側で 'git rebase $CUR' を実行し、解決してから再試行してください。"
    exit 1
  fi

  if [ "$DRY" -eq 1 ]; then
    echo "  ✓ クリーンにマージ可能"
    git merge --abort 2>/dev/null || git reset --hard "$BEFORE" >/dev/null
    continue
  fi

  # MERGE_HEAD が無い = 取り込むものが無かった (既にマージ済み)。
  # ここで commit すると失敗するので、スキップして次へ進む。
  if [ ! -f "$(git rev-parse --git-dir)/MERGE_HEAD" ]; then
    echo "  - 取り込む変更がありません (既にマージ済み)。スキップします。"
    continue
  fi

  if ! git commit --no-edit >/dev/null; then
    echo "  ✗ マージコミットの作成に失敗しました。統合を中止します。" >&2
    git merge --abort 2>/dev/null || git reset --hard "$BEFORE" >/dev/null
    exit 1
  fi
  echo "  ✓ マージしました"

  rollback() {
    echo "  → $BEFORE へ戻します"
    git reset --hard "$BEFORE"
  }

  if ! bash scripts/dev/check_migrations.sh; then
    echo "  ✗ マイグレーション整合に失敗。マージを取り消します。"
    rollback; exit 1
  fi

  if ! bash scripts/dev/preflight.sh --quick; then
    echo "  ✗ 統合後の検証に失敗。マージを取り消します。"
    rollback; exit 1
  fi
  echo "  ✓ 検証通過"
done

echo
echo "✓ すべてのブランチを統合しました。残りの worktree を同期してください:"
echo "    bash scripts/dev/wt.sh list"
