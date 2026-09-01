"""API が返す表示フラグに、**到達可能な**描画先があることを固定する。

## なぜ必要か（2026-09-01 の実測）

`is_sweet_spot` を画面へ出していたのは `IndicesTable.tsx` だけだった。
同コンポーネントは `RaceDetailClient.tsx` に置き換えられた際に参照が
0 件になり（＝どこからも import されない死んだファイル）、描画だけが
静かに落ちた。バックエンドは `races.py` で毎回 `is_sweet_spot` を算出して
返し続けていたため、**エラーもログも出ず、型エラーにもならない**。
CLAUDE.md も「IndicesTable.tsx で赤字表示」と書いたまま残っていた。

「そのフラグを書いているファイルが存在するか」では捕まらない
（死んだファイルが条件を満たしてしまう）。**src/app から辿れるか**で
判定する必要がある。

同じ型は地方でも起きていた（版ガードで推奨バッジが不到達）。
フロントに Vitest が入っていないため、静的な到達可能性検査をここに置く。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

# (画面, 表示フラグ) の組。
#
# 🔴 「どこか到達可能なファイルに出ていればよい」では不十分。別の柱の画面が
#    同じフラグ名を持っていると素通りする（実際 is_sweet_spot は地方のレース詳細に
#    もあるため、中央のレース詳細から描画が消えても検知できなかった）。
#    **その画面から辿れるか**で判定する。
REQUIRED_DISPLAY_FLAGS = [
    ("races/[id]", "is_sweet_spot"),   # スイートスポット該当馬（馬名を赤字）
    ("races/[id]", "is_cut_off"),      # 着外率による足切り（グレーアウト）
    ("races/[id]", "recommend_rank"),  # 軸の信頼度 tier
    ("races/[id]", "dm_signals"),      # 穴 / 特穴バッジ
]

_IMPORT_RE = re.compile(r"""^\s*(?:import|export)\b[^'"]*from\s*['"]([^'"]+)['"]""", re.M)
_SIDE_EFFECT_IMPORT_RE = re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.M)
_EXTS = (".tsx", ".ts", ".jsx", ".js")


def _resolve(spec: str, importer: Path) -> Path | None:
    """import 指定子をファイルへ解決する。外部パッケージは None。"""
    if spec.startswith("@/"):
        base = FRONTEND_SRC / spec[2:]
    elif spec.startswith("."):
        base = (importer.parent / spec).resolve()
    else:
        return None  # node_modules
    for cand in (base, *(base.with_suffix(e) for e in _EXTS), *(base / f"index{e}" for e in _EXTS)):
        if cand.is_file():
            return cand
    return None


def _reachable_from(entries: list[Path]) -> set[Path]:
    """与えたエントリから import を辿って到達できるファイル集合。"""
    seen: set[Path] = set()
    stack = list(entries)
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in list(_IMPORT_RE.finditer(text)) + list(_SIDE_EFFECT_IMPORT_RE.finditer(text)):
            target = _resolve(m.group(1), f)
            if target is not None and target not in seen:
                stack.append(target)
    return seen


def _entries_for(route: str) -> list[Path]:
    d = FRONTEND_SRC / "app" / route
    return [p for p in d.glob("*") if p.is_file() and p.suffix in _EXTS and p.stem == "page"]


def test_画面のエントリが解決できている() -> None:
    """解決器が壊れると全テストが素通りするので、規模そのものを検査する。"""
    for route, _ in REQUIRED_DISPLAY_FLAGS:
        assert _entries_for(route), f"画面 {route} の page が見つかりません"
    reach = _reachable_from(_entries_for("races/[id]"))
    assert len(reach) > 10, (
        f"races/[id] から辿れるファイルが {len(reach)} 件しかありません。"
        " import 解決が壊れている可能性があります（この検査が素通りになります）。"
    )


@pytest.mark.parametrize(("route", "flag"), REQUIRED_DISPLAY_FLAGS)
def test_表示フラグに到達可能な描画先がある(route: str, flag: str) -> None:
    """型定義（lib/api.ts）だけに出ていても「描いている」ことにはならない。"""
    api_ts = FRONTEND_SRC / "lib" / "api.ts"
    reach = _reachable_from(_entries_for(route))
    if any(p != api_ts and flag in p.read_text(encoding="utf-8") for p in reach):
        return

    orphans = sorted(
        str(p.relative_to(FRONTEND_SRC))
        for p in FRONTEND_SRC.rglob("*")
        if p.is_file() and p.suffix in _EXTS and p != api_ts
        and flag in p.read_text(encoding="utf-8")
    )
    raise AssertionError(
        f"画面 {route} に `{flag}` を描画している到達可能なコンポーネントがありません。"
        f" バックエンドは算出して返し続けるのに画面には出ない状態です。"
        + (
            f"\n参照はあるが この画面から辿れない ファイル: {orphans}"
            if orphans
            else "\nそもそもフロントのどこからも参照されていません。"
        )
    )
