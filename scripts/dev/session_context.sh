#!/usr/bin/env bash
# ============================================================================
# セッション開始時に「いまどこに居るか」をモデルへ渡す（SessionStart フックの実体）
#
# 🔴 clone を柱ごとに分けた結果、**古いコードで現状を調べる**事故の余地が3つできた:
#
#   (a) 自分の clone が origin/main から遅れている
#   (b) 「現在」が2つある — origin/main（マージ済み）と、**本番が実際に動かしている
#       コード**。2026-09-23 の実測では main が 73804bdd なのに、その日の競輪の入稿を
#       作ったのは d42e2658 だった（朝バッチ 07:00 JST がデプロイ 09:33 JST より前）
#   (c) 他の clone の作業が見えない
#
# ここで毎回それを突きつける。CLAUDE.md「🔴🔴 検証の作法 — 測る前に本番コードを読む」
# （1日で6回踏んだ記録）を手作業から機械化するのが目的。
#
# ⚠️ SessionStart フックのタイムアウトは 30 秒。実測 約4秒（fetch 2.4 / gh 1.8）。
#    重い処理を足さないこと。ネットワークが死んでいても**必ず exit 0 で抜ける**
#    （セッションの開始を人質に取らない）。
# ============================================================================
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0

git fetch origin --prune --quiet 2>/dev/null

BR="$(git symbolic-ref --short -q HEAD || echo '(detached)')"
BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
DIRTY="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
MAIN_SHA="$(git rev-parse --short origin/main 2>/dev/null)"

echo "━━ kiseki: $(basename "$ROOT") ━━"
printf 'ブランチ %s' "$BR"
[ "$BEHIND" != "0" ] && printf '  🔴 origin/main から %s コミット遅れ' "$BEHIND"
[ "$DIRTY" != "0" ] && printf '  ⚠️ 未コミット %s 件' "$DIRTY"
echo
echo "origin/main  $MAIN_SHA  $(git log -1 --format=%s origin/main 2>/dev/null | cut -c1-56)"

# ---- 本番が実際に動かしているコミット ----
# run の conclusion が success ＝ deploy ジョブも成功（deploy は同じ workflow 内なので
# 落ちれば run 全体が failure になる。2026-09-23 に実測で確認）。
#
# 🔴 **API の返却順に依存しないこと。** `first(.[] | select(...))` で書いたところ、
#    一度だけ 12 日前のコミットを「本番」として返した（再現せず・原因未特定）。
#    ここは「本番が何で動いているか」を答える場所で、**黙って古い値を返すのが
#    最悪の壊れ方**なので、createdAt で明示的に並べ替えてから先頭を取る。
DEP="$(gh run list --branch main --limit 20 \
        --json conclusion,headSha,createdAt,displayTitle \
        -q 'sort_by(.createdAt) | reverse | map(select(.conclusion=="success")) | .[0]
             | "\(.headSha[0:8]) \(.createdAt[5:16])Z \(.displayTitle[0:44])"' \
      2>/dev/null)"
if [ -n "$DEP" ]; then
  DEP_SHA="${DEP%% *}"
  if [ "$DEP_SHA" = "$MAIN_SHA" ]; then
    echo "本番        $DEP  ✅ main と同じ"
  else
    echo "🔴 本番      $DEP"
    echo "   ＝ main はこれより先。**「現状こうなっている」は本番コードで確かめること**"
    echo "   差分: git diff $DEP_SHA..origin/main -- <パス>"
  fi
  echo "   ⚠️ バッチが動いた時刻とデプロイ時刻の前後は別途確かめる（07:00 の朝バッチが"
  echo "      06:59 のデプロイより前に走ることがある）"
fi

# ---- 他の clone / 他セッションの作業 ----
PRS="$(gh pr list --state open --limit 20 --json number,headRefName \
        -q '.[] | "#\(.number) \(.headRefName)"' 2>/dev/null)"
if [ -n "$PRS" ]; then
  echo "他に開いている PR:"
  echo "$PRS" | sed 's/^/  /'
fi

bash "$ROOT/scripts/dev/scan_collisions.sh" 2>/dev/null | grep -A20 '衝突リスク' | head -24

exit 0
