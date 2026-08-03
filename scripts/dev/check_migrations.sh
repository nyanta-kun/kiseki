#!/usr/bin/env bash
# ============================================================================
# Alembic マイグレーション整合チェック (並列開発の最重要ガード)
#
# 本リポジトリは過去に fix/migration-down-revision, fix/migration-id-collision,
# fix/migration-revision-conflict という 3 本の修復ブランチを生んでいる。
# 原因は「複数ブランチが同時に revision を生成し、head が分岐する」こと。
# これを *マージ前に* 機械的に検出する。
#
# 検査項目:
#   1. head が 1 つだけか (multiple heads = マージ時に必ず事故る)
#   2. revision ID の重複がないか
#   3. down_revision が実在するか (dangling parent)
#   4. ファイル名の接頭辞と revision ID が一致しているか (追跡性)
#
# DB 接続も alembic のインストールも不要 (ファイル解析のみ) なので CI で高速。
#
# 使い方: bash scripts/dev/check_migrations.sh
# 終了コード: 0=OK / 1=問題あり
# ============================================================================
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
VERSIONS_DIR="${1:-$REPO_ROOT/backend/alembic/versions}"

if [ ! -d "$VERSIONS_DIR" ]; then
  echo "ERROR: versions ディレクトリが見つかりません: $VERSIONS_DIR" >&2
  exit 1
fi

python3 - "$VERSIONS_DIR" <<'PYEOF'
import ast, os, re, sys

versions_dir = sys.argv[1]
revs, downs, problems, warnings = {}, {}, [], []

def literal(src, name):
    """revision / down_revision の代入値を安全に取り出す。"""
    m = re.search(r'^%s(?:\s*:\s*[^=]+)?\s*=\s*(.+?)$' % name, src, re.M)
    if not m:
        return "__ABSENT__"
    try:
        return ast.literal_eval(m.group(1).strip())
    except Exception:
        return "__UNPARSED__"

for fn in sorted(os.listdir(versions_dir)):
    if not fn.endswith(".py") or fn.startswith("__"):
        continue
    src = open(os.path.join(versions_dir, fn), encoding="utf-8").read()
    rev = literal(src, "revision")
    down = literal(src, "down_revision")

    if not isinstance(rev, str):
        problems.append(f"[revision不正] {fn}: revision を文字列として読めません ({rev!r})")
        continue
    revs.setdefault(rev, []).append(fn)
    downs[rev] = (fn, down)

    prefix = fn.split("_")[0]
    if prefix != rev:
        warnings.append(f"[命名不一致] {fn}: ファイル名接頭辞 '{prefix}' != revision '{rev}'")

# 1. revision ID 重複
for rev, files in revs.items():
    if len(files) > 1:
        problems.append(f"[ID重複] revision '{rev}' が {len(files)} ファイルで使われています: {files}")

# 2. dangling parent + head 算出
known = set(revs)
has_child = set()
for rev, (fn, down) in downs.items():
    if down is None:
        continue
    parents = [down] if isinstance(down, str) else (list(down) if isinstance(down, (tuple, list)) else [])
    if down == "__ABSENT__":
        problems.append(f"[down_revision欠落] {fn}: down_revision が定義されていません")
        continue
    if down == "__UNPARSED__":
        problems.append(f"[down_revision解析不能] {fn}: 手動確認が必要です")
        continue
    for p in parents:
        if p not in known:
            problems.append(f"[親不明] {fn}: down_revision '{p}' に対応する revision がありません")
        has_child.add(p)

heads = sorted(r for r in known if r not in has_child)

print(f"マイグレーション数: {len(revs)}")
print(f"head: {len(heads)} 個 -> {heads}")

if len(heads) > 1:
    problems.insert(0, f"[HEAD分岐] head が {len(heads)} 個あります: {heads}\n"
                       "    → 後からマージする側のブランチで、最新 head を down_revision に付け替えてください。\n"
                       "    → merge revision の量産は履歴を読めなくするため原則禁止。")
elif len(heads) == 0 and revs:
    problems.insert(0, "[循環] head が 0 個です。リビジョングラフが循環しています。")

strict = os.environ.get("MIGRATION_CHECK_STRICT") == "1"

if warnings:
    print("\n=== 警告 (履歴の追跡性の問題。マージはブロックしません) ===")
    for w in warnings:
        print(" WARN " + w)

if problems:
    print("\n=== 致命的な問題を検出しました ===")
    for p in problems:
        print(" NG " + p)
    sys.exit(1)

if warnings and strict:
    print("\nMIGRATION_CHECK_STRICT=1 のため警告を失敗として扱います。")
    sys.exit(1)

print("\nOK: head は単一、revision ID 重複・親不明はありません。")
PYEOF
