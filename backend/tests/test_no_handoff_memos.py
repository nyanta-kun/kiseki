"""日付入りの引き継ぎメモを再び積み上げないことを固定する。

## なぜ必要か

2026-08-23〜09-02 に、ルートへ `HANDOFF_YYYY-MM-DD.md` が 6 本積み上がった。
新しいメモが古いメモを上書きしないため**どれが最新か分からず**、索引
`HANDOFF.md` とそれを縛るテストを足しても膨らみ続けた。

2026-09-23 に全部を現況台帳 `docs/PROJECT_STATUS.md`（実装状況・検証結果・未検証）へ
統合して削除した。以後は**台帳を書き換える**運用にする。ここではその逆戻り
（ルートや keirin/ に引き継ぎメモを置くこと）を機械的に止める。

⚠️ 台帳の中身の鮮度までは検査しない（できない）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS = REPO_ROOT / "docs" / "PROJECT_STATUS.md"

#: 引き継ぎメモと見なすファイル名（大文字小文字を区別しない）。
HANDOFF_NAME = re.compile(r"(handoff|continuation|引き?継ぎ?)", re.IGNORECASE)

#: 引き継ぎメモを置きがちな場所。docs/ 配下の検証記録は対象外。
WATCHED_DIRS = (REPO_ROOT, REPO_ROOT / "keirin", REPO_ROOT / "docs")


def test_現況台帳が存在する() -> None:
    assert STATUS.is_file(), "現況台帳 docs/PROJECT_STATUS.md がありません"


def test_引き継ぎメモを新設していない() -> None:
    found = sorted(
        str(p.relative_to(REPO_ROOT)) for d in WATCHED_DIRS for p in d.glob("*.md") if HANDOFF_NAME.search(p.name)
    )
    assert not found, (
        f"引き継ぎメモが置かれています: {found}。"
        " 日付入りのメモを足さず、docs/PROJECT_STATUS.md を直接書き換えてください"
        "（メモを積むと、どれが最新か分からなくなるため）。"
    )
