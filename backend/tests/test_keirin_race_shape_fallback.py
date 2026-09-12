"""推奨外レースの型（A〜F）が**全車数**で出ることを固定する（2026-09-12）。

## 背景（実際に起きていたこと）

`/keirin` の一覧は型ラボの型をバッジで出すが、その型は
`keirin.type_lab_picks`（＝**売る商品**の行）から引いていた。商品を組むのは
`type_lab_daily.sh` が回す **7車と9車だけ**なので、5車・6車・8車のレースは
型が NULL になり、**同じ推奨外なのに型が出る行と出ない行が混ざっていた**
（2026-09-12 実測: 直近1週間で 31レース＝5車2・6車25・8車4）。

型判定そのもの（`keirin/src/type_lab.race_shape`）は 3着内率と並びしか見ないので
車数に依らない。`keirin/scripts/build_race_shapes.py` が全レースぶんを
`keirin.race_shapes` へ書き、API はそこへ落ちる。

## 何を守るか

1. `include_all` の SQL が `keirin.race_shapes` を結合していること
2. 応答の `type_lab_type` が「商品の型 → 無ければ race_shapes の型」の順で決まること

⚠️ 「商品の型を優先する」向きは落とせない。`type_lab_picks.type_label` は
   売った行に**焼き付いた**値で、後からモデルが再学習されても動かない。
   `race_shapes` は表示専用で毎回上書きされるので、売った商品の説明には使えない。
"""
from __future__ import annotations

import inspect
import re

from src.api import keirin_router


def _picks_source() -> str:
    return inspect.getsource(keirin_router.get_picks)


def test_include_all_query_joins_race_shapes():
    src = _picks_source()
    assert re.search(r"LEFT JOIN\s+keirin\.race_shapes\s+rs", src), (
        "include_all の SQL が keirin.race_shapes を結合していない。"
        " これが無いと 5車・6車・8車のレースだけ型が出ない")
    assert re.search(r"rs\.race_key\s*=\s*wr\.race_key", src), (
        "race_shapes の結合キーが race_key になっていない")
    assert re.search(r"rs\.type_label\s+AS\s+shape_type", src), (
        "外側の SELECT が shape_type を取り出していない"
        "（LATERAL 以外の JOIN でも、選び忘れると実行時にしか落ちない）")


def test_type_lab_type_falls_back_to_race_shape():
    """商品の型を優先し、無ければ race_shapes の型へ落ちる。"""
    src = _picks_source()
    m = re.search(r'"type_lab_type":\s*(.+?),\n', src)
    assert m, "type_lab_type の組み立てを見つけられなかった"
    expr = m.group(1)
    assert 'r.get("tl_type")' in expr and 'r.get("shape_type")' in expr, (
        f"type_lab_type が両方を見ていない: {expr!r}")
    assert expr.index('r.get("tl_type")') < expr.index('r.get("shape_type")'), (
        "売った商品に焼き付いた型（tl_type）を先に見ること。"
        " race_shapes は表示専用で毎回上書きされる")
