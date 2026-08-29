"""`get_summary` の並列化を構造で固定する（2026-08-23）。

## 背景

`/keirin/summary` は dict リテラルの中で8本を `await` しており、
**Python が逐次に評価する**ため全部が足し算になっていた。
本番実測（合計 2,216ms）: year 722 / month 655 / today 410 / paper_total 217 / 他。

2026-08-21 にペーパー補完が3期間へ増えてから、この直列がそのまま
`/keirin` の表示時間になっていた（実測 逐次 2,291ms → 並列 1,390ms）。

🔴 **戻しても結果は変わらず、遅くなるだけ**（＝レビューでも本番でも気づけない）
   ので、ここで構造を固定する。
"""
from __future__ import annotations

import ast
import inspect
import re

from src.api import keirin_router as R

SRC = inspect.getsource(R.get_summary)


def test_uses_gather():
    """🔴 逐次 await へ戻さないこと。"""
    assert "asyncio.gather" in SRC, (
        "3期間 + 通算は並行に走らせる。dict リテラル内の await は逐次評価になる"
    )


def test_does_not_share_one_session_across_gather():
    """🔴 **同じ AsyncSession を並行に使ってはいけない。**

    SQLAlchemy はセッションの同時使用を許さず、実行時に
    `another operation is in progress` で落ちる。塊ごとに別セッションを張ること。
    """
    assert "AsyncSessionLocal()" in SRC, "並行する塊は別セッションで張る"
    # gather に渡している引数のうち、リクエスト自身の db をそのまま使うのは1つだけ
    m = re.search(r"asyncio\.gather\((.*?)\n    \)", SRC, re.S)
    assert m, "gather の呼び出しが読み取れない"
    args = m.group(1)
    assert args.count("_in_new_session") == 3, (
        "db をそのまま使うのは1塊だけ・残りは新しいセッション"
    )


def test_connection_fanout_is_bounded():
    """接続本数を増やしすぎないこと（プールは pool_size 5 + max_overflow 15）。

    細かく割っても律速は一番重い `year` の集計なので、4分割より先は縮まない。
    """
    assert SRC.count("_in_new_session(") <= 4


def test_result_keys_unchanged():
    """フロントが読むキーを変えていないこと。"""
    for key in ("today", "month", "year", "visible_ranks"):
        assert f'"{key}"' in SRC


def test_paper_total_is_not_computed():
    """🔴 `paper_total` を復活させるなら**表示も一緒に戻すこと**（2026-08-29）。

    2026-08-24 に画面から外して以来 frontend の参照はゼロなのに、
    毎リクエスト `picks_history` を2回フルスキャンし、そのためだけに
    DB コネクションを1本（pool_size 5 のうち）掴んでいた。
    集計する関数（`_aggregate_paper`）は残してあるので、戻すのは1行で済む。
    """
    called = {
        getattr(n.func, "id", None) or getattr(n.func, "attr", None)
        for n in ast.walk(ast.parse(SRC.lstrip())) if isinstance(n, ast.Call)
    }
    assert "_aggregate_paper" not in called, (
        "画面に出ていない集計をレスポンスのために回さないこと"
    )


def test_paper_merge_still_applied_to_all_three_periods():
    """⚠️ 3期間とも `_merge_paper_into` を通すこと。

    当年だけに効かせていた頃は、実販売開始前へ日付を遡ると当日・当月が
    全部 0 になり「その日に何を推奨していたか」が画面から消えていた
    （2026-08-22 是正）。並列化でこれを落とさない。
    """
    assert "_merge_paper_into" in SRC
    assert "_paper_for_period" in SRC
