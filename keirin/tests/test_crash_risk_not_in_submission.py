"""落車リスクが netkeirin の入稿データへ漏れないことを固定する（2026-08-21）。

🔴 **ユーザー判断（2026-08-21）**: 落車リスクは常に存在するものなので大々的に
   提示せず、**netkeirin の入稿データには含めない**。Web 表示で有用性を
   監視するレベルに留める。

入稿データ＝ netkeirin へ送るタイトル・コメント・買い目（`bet_detail`）。
ここへ混ぜると**公開後に差し替えができない**ので、構造で塞いでおく。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: 入稿の文面・買い目を組み立てる経路
SUBMIT_PATHS = [
    "scripts/netkeirin_submit_wt.py",
    "scripts/submit_marquee_wt.py",
    "scripts/update_netkeirin_templates.py",
    "src/strategy_wt.py",
]


@pytest.mark.parametrize("rel", SUBMIT_PATHS)
def test_submission_path_does_not_reference_crash_risk(rel: str) -> None:
    p = REPO / rel
    if not p.exists():
        pytest.skip(f"{rel} が無い")
    src = p.read_text(encoding="utf-8")
    for token in ("crash_risk", "keirin_crash_risk", "落車リスク"):
        assert token not in src, (
            f"{rel} が落車リスクを参照している。入稿データへ含めない方針"
            "（2026-08-21 ユーザー判断）に反する")


def test_the_index_itself_still_exists_on_the_web_side() -> None:
    """指標そのものは残っていること（消したのではなく、出す場所を絞っただけ）。"""
    svc = REPO.parent / "backend" / "src" / "services" / "keirin_crash_risk.py"
    assert svc.exists(), "指標の実装ごと消えている"
    api = (REPO.parent / "backend" / "src" / "api" / "keirin_router.py").read_text(
        encoding="utf-8")
    assert '"crash_risk"' in api, "API から落ちている（監視できなくなる）"
