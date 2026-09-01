"""ルートの引き継ぎメモが索引 `HANDOFF.md` に載っていることを固定する。

## なぜ必要か

引き継ぎメモは日付ごとのスナップショットで積み上がるが、**どれが最新かを示す
仕組みが無いままルートに並んでいた**（監査時点で 6 本）。ファイル名だけを見ても
どれを読めばいいか判断できず、古い「次にやること」を実行する事故につながる。

索引 `HANDOFF.md` を作っても、**次に書かれたメモが索引に載らなければ同じ状態に戻る**。
索引は「書き忘れても誰も気づかない」種類の文書なので、機械的に縛る。

⚠️ 中身の鮮度までは検査しない（できない）。ここが守るのは
「ルートに置いた引き継ぎメモが索引から辿れること」だけ。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX = REPO_ROOT / "HANDOFF.md"

#: ルート直下の引き継ぎメモ。名前の付け方が違うものはここへ足す。
EXTRA_HANDOFFS = ("keiba-agent-handoff.md",)


def _handoff_files() -> list[Path]:
    files = sorted(REPO_ROOT.glob("HANDOFF_*.md"))
    files += [REPO_ROOT / n for n in EXTRA_HANDOFFS if (REPO_ROOT / n).is_file()]
    return files


def test_索引が存在する() -> None:
    assert INDEX.is_file(), "HANDOFF.md（引き継ぎメモの索引）がありません"


def test_全ての引き継ぎメモが索引に載っている() -> None:
    text = INDEX.read_text(encoding="utf-8")
    missing = [f.name for f in _handoff_files() if f.name not in text]
    assert not missing, (
        f"索引 HANDOFF.md に載っていない引き継ぎメモがあります: {missing}。"
        " 新しく書いたら索引の先頭に1行足してください"
        "（どれが最新かは索引でしか分からないため）。"
    )


def test_索引が実在しないファイルを指していない() -> None:
    """整理でファイルを消したのに索引だけ残る、の逆側。"""
    import re

    text = INDEX.read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\((HANDOFF_[^)]+\.md|keiba-agent-handoff\.md)\)", text))
    dangling = sorted(n for n in linked if not (REPO_ROOT / n).is_file())
    assert not dangling, f"索引が実在しないファイルを指しています: {dangling}"


def test_各メモが索引への導線を持つ() -> None:
    """索引から辿れても、メモ側から戻れないと「これは古いのか」が分からない。"""
    missing = [
        f.name
        for f in _handoff_files()
        if "HANDOFF.md" not in f.read_text(encoding="utf-8")[:1200]
    ]
    assert not missing, (
        f"冒頭に索引への導線が無い引き継ぎメモがあります: {missing}。"
        " 『これはスナップショットであって運用ルールの正本ではない』ことを"
        " 冒頭で示してください。"
    )
