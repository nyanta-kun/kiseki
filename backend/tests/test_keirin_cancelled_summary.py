"""取り消したレースの「そのまま売っていたら」サマリーの規則を固定する（2026-08-24）。

ユーザー要望「レビューページのサマリー表示の下に、入稿したが取り消したレースを
そのまま入稿していたらのサマリーを追加して」（母集団は**取り消したレースのみ**）。

🔴 **実績ではない。** 売っていないので netkeirin の成績にも上の `summary` にも
   入らない。落とした判断が正しかったかを見るための参考値。
   既存の約束（`winning_combos` の項）と同じ扱いで、**文言で必ず区別する**。

ここで固定するのは4つ:
  1. 採点は**実績と同じ経路**（`_fetch_settled_submissions`＝確定オッズ）を通ること
  2. 母集団が**取消のみ**（`deleted_at IS NOT NULL`）であること
  3. 実績サマリーの母集団に取消が**混ざらない**こと
  4. 画面が参考値だと分かる見出しを必ず出すこと
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ROUTER = (ROOT / "backend" / "src" / "api" / "keirin_router.py").read_text("utf-8")


def test_取消のみを採点する口がある():
    assert "deleted_only: bool = False" in ROUTER, "取消のみの引数が無い"
    m = re.search(r'deleted_cond = \("([^"]+)" if deleted_only\s*\n?\s*else "([^"]+)"\)',
                  ROUTER)
    assert m, "deleted_cond の定義が見つからない"
    assert m.group(1) == "ns.deleted_at IS NOT NULL", "取消のみの条件が違う"
    assert m.group(2) == "ns.deleted_at IS NULL", "既定が『売った分だけ』でない"


def test_採点は実績と同じ経路を通る():
    """🔴 画面で `bet_detail` のオッズから計算しない。実績は確定オッズ採点なので
       別経路にすると2つの表が別の作り方の数字になる。"""
    blk = ROUTER[ROUTER.index("summary_cancelled = {") - 1200:
                 ROUTER.index("summary_cancelled = {")]
    assert "_fetch_settled_submissions(" in blk
    assert "deleted_only=True" in blk


def test_実績サマリーには取消が混ざらない():
    """🔴 `sold` は submitted / published だけ。取消を足す口を作らない。"""
    m = re.search(r"sold = \[x for x in items if x\[.status.\] in \(([^)]+)\)\]", ROUTER)
    assert m, "実績サマリーの母集団が見つからない"
    assert "DELETED" not in m.group(1).upper()


def test_APIが両方を返す():
    assert '"summary": summary,' in ROUTER
    assert '"summary_cancelled": summary_cancelled,' in ROUTER


def test_画面が参考値だと分かる見出しを出す():
    tsx = (ROOT / "frontend" / "src" / "app" / "keirin" / "review"
           / "ReviewClient.tsx").read_text("utf-8")
    assert "summaryCancelled" in tsx, "画面が受け取っていない"
    assert "参考値" in tsx, "参考値だと分かる文言が無い"
    assert "実績には含みません" in tsx, "実績と区別する文言が無い"
    # 🔴 見出しは caption で必ず出す（同じ形の表が2つ並ぶため）
    assert "caption" in tsx


def test_ページが受け渡している():
    page = (ROOT / "frontend" / "src" / "app" / "keirin" / "review"
            / "page.tsx").read_text("utf-8")
    assert "summaryCancelled={proposals.summary_cancelled}" in page
