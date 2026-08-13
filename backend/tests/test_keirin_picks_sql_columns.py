"""`/keirin/picks?include_all=true` の SQL に対する構造テスト（2026-08-14）。

## 背景（実際に起きた障害）

PR #135 で「入稿の出自（穴埋め / 手動）」をバッジ表示するため外側の SELECT へ
`ns.origin` を足したが、`ns` の実体である **LATERAL サブクエリの SELECT には
`origin` を足し忘れた**。SQL は文字列なので静的解析では落ちず、
`UndefinedColumnError: column ns.origin does not exist` が**実行時にだけ**出る。

結果、`include_all=true`（Web の `/keirin` が常に使う経路）が 500 を返し続け、
画面は「ピックの取得に失敗しました」で**丸ごと空**になった。一方で
`include_all` なしの経路は無事だったため、API 単体を叩くだけでは気づけなかった。

## 何を守るか

外側の SELECT が参照する `ns.<列>` が、LATERAL サブクエリの SELECT リストに
**すべて含まれていること**。DB を用意せず、SQL 文字列の構造だけで検査する。

⚠️ このテストは「SQL の書き方」に依存する。クエリの組み立て方を変えたときは
   本テストも追随させること（黙って素通りさせるより落ちるほうがよい）。
"""
from __future__ import annotations

import inspect
import re

from src.api import keirin_router


def _picks_source() -> str:
    return inspect.getsource(keirin_router.get_picks)


def _include_all_query() -> str:
    """LATERAL を含む SQL 文字列だけを切り出す。

    ⚠️ 関数全体を対象にしてはいけない。同じ関数には `ns` を
       **テーブルそのものの別名**として使う別のクエリがあり、
       そちらの `ns.race_key` 等まで「LATERAL に無い」と誤検出する。
    """
    src = _picks_source()
    m = re.search(r"LEFT JOIN LATERAL \(", src)
    assert m, ("picks の LATERAL サブクエリを見つけられなかった。"
               " クエリの書き方を変えたなら本テストも追随させること")
    start = src.rfind('text(f"""', 0, m.start())
    end = src.find('"""', m.end())
    assert start != -1 and end != -1, "LATERAL を含む SQL 文字列を切り出せなかった"
    return src[start:end]


def test_lateral_selects_every_referenced_ns_column():
    src = _include_all_query()

    m = re.search(
        r"LEFT JOIN LATERAL \((.*?)\) ns ON TRUE", src, re.DOTALL)
    assert m, "LATERAL の閉じ方（`) ns ON TRUE`）が見つからなかった"
    lateral = m.group(1)

    sel = re.search(r"SELECT\s+(.*?)\s+FROM", lateral, re.DOTALL)
    assert sel, f"LATERAL の SELECT 句を解釈できなかった: {lateral!r}"
    selected = {
        c.strip().split()[-1].split(".")[-1]
        for c in sel.group(1).split(",") if c.strip()
    }

    # 外側（LATERAL の中身を除いた本文）が参照する ns.<列>
    outer = src.replace(lateral, "")
    referenced = set(re.findall(r"\bns\.([a-z_]+)", outer))

    missing = referenced - selected
    assert not missing, (
        f"外側が参照する ns.{{{', '.join(sorted(missing))}}} が LATERAL の"
        f" SELECT に無い（SELECT: {sorted(selected)}）。"
        " 実行時に UndefinedColumnError で 500 になり、"
        " Web は『ピックの取得に失敗しました』で空になる")


def test_origin_is_selected():
    """🔴 実際に落ちた列を名指しで固定する（回帰の本体）。"""
    m = re.search(r"LEFT JOIN LATERAL \((.*?)\) ns ON TRUE",
                  _picks_source(), re.DOTALL)
    assert m and "origin" in m.group(1), \
        "LATERAL が origin を SELECT していない（PR #135 と同じ壊れ方）"
