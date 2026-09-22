#!/bin/bash
# Claude Code が `git commit` を実行する前に、staged 内容を gitleaks で検査する。
#
# 位置づけ: pre-commit（git hook）が入っていない clone でも、
# エージェント経由のコミットだけは必ずここを通る。二重の網。
#
# 2026-09-23: hrdb_user のパスワードが平文でコミットされていたことが判明し
# ローテーションした。検知できなければ意味が無いので、実際に漏れた形
# （postgresql://user:pass@host）は .gitleaks.toml のカスタムルールで拾う。
set -uo pipefail

DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
cd "$DIR" 2>/dev/null || exit 0

deny() {
  /usr/bin/python3 -c '
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": sys.argv[1],
}}))' "$1"
  exit 2
}

if ! command -v gitleaks >/dev/null 2>&1; then
  # 入っていないことを黙って通さない。止めはしないが必ず知らせる。
  echo "WARN: gitleaks が未導入のため秘密情報の検査をしていません（brew install gitleaks）" >&2
  exit 0
fi

OUT="$(gitleaks git --staged --no-banner --redact -v -c .gitleaks.toml 2>&1)" && exit 0

# gitleaks は色付きで stderr に出す。ANSI を落としてから抜き出す。
DETAIL="$(printf '%s' "$OUT" | sed -E 's/\x1b\[[0-9;]*m//g' | grep -E '^(Finding|RuleID|File|Line|Secret):' | head -12)"
deny "秘密情報らしき文字列が staged に含まれています。値を無効化（ローテーション）してから取り除いてください。
${DETAIL:-$(printf '%s' "$OUT" | tail -5)}"
