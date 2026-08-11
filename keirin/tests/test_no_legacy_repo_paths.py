"""keirin のコード・スクリプトが「kiseki の外にある keirin」を名指ししないこと。

## 背景（2026-08-10 / 08-11 に実際に踏んだ）

keirin は 2026-08-10 に `~/keirin`（VPS）・`~/GitHub/keirin`（Mac）から
`<kiseki>/keirin` へ移設された。keirin のスクリプトは基本的に
`cd "$(dirname "$0")/.."` や `Path(__file__)` で自分の位置から解決するので
移動に強いが、**その方式が効かない絶対パスだけが壊れた**:

- `sync_models_to_vps.sh` の `REMOTE_DIR`（リモート側の絶対パス）
- kiseki 側 `scripts/scrape_netkeirin_sales.sh` の `KEIRIN_DIR`
- `keirin-webhook.service`（systemd unit）
- 分析スクリプト 78本の `REPO = Path("/Users/ysuzuki/GitHub/keirin")`

最後のものは cron に無いため移設時の疎通確認をすり抜け、**次に検証作業を
始めた時点で初めて落ちる**（2026-08-11 に一括是正）。

## 守る不変条件

コード・スクリプト中に **kiseki の外を指す keirin の絶対パスを書かない。**
リポジトリルートは `Path(__file__).resolve().parents[1]` /
`$(cd "$(dirname "$0")/.." && pwd)` のように**自分の位置から導く**こと。

⚠️ 「ワークツリーだから本番リポジトリへ逃がす」分岐も禁止。ローカル SQLite が
   廃止（2026-07-22）された今、ワークツリーでも自分のツリーが正しい。
"""

from __future__ import annotations

import re
from pathlib import Path

# /Users/xxx/... または /home/xxx/... の末尾が keirin で終わる絶対パス。
# 例: /Users/ysuzuki/GitHub/keirin, /home/ysuzuki/keirin
_LEGACY_PATH = re.compile(r"""(?:/Users|/home)/[^/\s"'`]+/(?:[^/\s"'`]+/)*keirin(?=[/\s"'`.]|$)""")

_REPO_ROOT = Path(__file__).resolve().parents[1]        # <kiseki>/keirin
_KISEKI_ROOT = _REPO_ROOT.parent

_SUFFIXES = {".py", ".sh", ".service", ".yml", ".yaml"}
_SKIP_DIRS = {".venv", "data", ".git", "node_modules", "__pycache__"}


_SELF = Path(__file__).resolve()


def _files_to_scan() -> list[Path]:
    out: list[Path] = []
    for root in (_REPO_ROOT, _KISEKI_ROOT / "scripts"):
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.suffix not in _SUFFIXES or not p.is_file():
                continue
            if _SKIP_DIRS & set(p.relative_to(_KISEKI_ROOT).parts):
                continue
            if p.resolve() == _SELF:      # 自分は旧パスの実例を持つので除外
                continue
            out.append(p)
    return out


def test_no_keirin_path_outside_kiseki() -> None:
    """🔴 kiseki の外にある keirin を絶対パスで名指ししないこと。

    `<何か>/kiseki/keirin` は正しい参照なので許す。
    """
    offenders: list[str] = []
    for p in _files_to_scan():
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in _LEGACY_PATH.finditer(line):
                if "/kiseki/keirin" in m.group(0):
                    continue
                offenders.append(f"{p.relative_to(_KISEKI_ROOT)}:{i}: {m.group(0)}")
    assert not offenders, (
        "kiseki の外を指す keirin の絶対パスが書かれています "
        "（2026-08-10 の移設で消えた場所です）:\n  "
        + "\n  ".join(offenders[:20])
        + "\nリポジトリルートは自ファイルの位置から導いてください。"
    )


def test_the_pattern_actually_matches_the_old_paths() -> None:
    """検査自体が機能していること（空振りで通っていないことの証明）。"""
    assert _LEGACY_PATH.search('REPO = Path("/Users/ysuzuki/GitHub/keirin")')
    assert _LEGACY_PATH.search("KEIRIN_HOME=/home/ysuzuki/keirin")
    assert _LEGACY_PATH.search("cd /home/ysuzuki/keirin/scripts")
    # 現行の正しい参照は拾わない
    for ok in ("/home/ysuzuki/GitHub/kiseki/keirin/data",
               "~/GitHub/kiseki/keirin/scripts/x.sh"):
        m = _LEGACY_PATH.search(ok)
        assert m is None or "/kiseki/keirin" in m.group(0), ok


def test_scan_actually_covers_files() -> None:
    """走査対象が空でないこと（0件を通過と誤認しない）。"""
    files = _files_to_scan()
    assert len(files) > 100, f"走査対象が少なすぎます: {len(files)}"
