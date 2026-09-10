"""POG ドラフトの規則と安全装置を固定する。

なぜ必要か（2026-09-10・統合 Phase 5 の 5e-2）:
    🔴 **次のドラフトは 2027年春**。実地で確かめられるのは半年以上先なので、
    規則が壊れても気づけない。移設元から写した判定をここに留める。

    特に「伏せ札」は漏れても例外が出ない。先に指名した人の馬が見えると
    ドラフトが成立しなくなるが、画面は普通に動いているように見える。
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.api import pog_draft_router as m

_SRC = Path(m.__file__).read_text(encoding="utf-8")


def test_書き込みは全て認証を要求する():
    tree = ast.parse(_SRC)
    unguarded: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        decos = [
            d for d in node.decorator_list
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and getattr(d.func.value, "id", None) == "router"
        ]
        if not decos:
            continue
        if any(d.func.attr == "websocket" for d in decos):
            continue  # WS は「変わった」としか送らない（下の検査で固定）
        anns = {
            getattr(a.annotation, "id", None)
            for a in node.args.args
            if a.annotation is not None
        }
        if "ApiKeyDep" not in anns:
            unguarded.append(node.name)
    assert not unguarded, f"認証が付いていないエンドポイント: {unguarded}"


def test_新しい指名は伏せて入る():
    """🔴 `visible = true` で入れると、**先に入れた人の指名を見てから
    指名できる**ようになりドラフトが成立しない。
    """
    body = _SRC.split("async def save_pick", 1)[1].split("@router.delete", 1)[0]
    assert "visible)" in body and "false)" in body, "指名が伏せて入っていない"


def test_盤面は伏せ札を出し分ける():
    """管理者と本人以外に `visible = false` の行を返さないこと。"""
    body = _SRC.split("async def get_draft", 1)[1].split("class RollOut", 1)[0]
    assert "p.visible OR :is_admin OR p.user_id = :viewer" in body


def test_確定は送られた人だけ触る():
    """送られてこなかった人の `pick_order` を消さないこと。

    移設元も `if (!('order' in target)) continue` で同じことをしていた。
    ここを全員一括更新にすると、確定済みの巡が巻き戻る。
    """
    body = _SRC.split("async def set_order", 1)[1].split("class SkipIn", 1)[0]
    assert "for t in body.targets:" in body
    assert "WHERE group_id = :g AND user_id = :u AND draft_order = :d" in body


def test_スキップ取消は指名済みの行を消さない():
    body = _SRC.split("async def set_skip", 1)[1].split("class RollIn", 1)[0]
    assert "netkeiba_horse_id IS NULL" in body, "馬を指名済みの行まで消してしまう"


def test_出目は1から6に限る():
    """DB 側の CHECK と二重にする（移設元は無検査だった）。"""
    body = _SRC.split("class RollIn", 1)[1].split("@router.post", 1)[0]
    assert body.count("ge=1, le=6") == 3


def test_通知は中身を送らない():
    """伏せ札が公開前に漏れる経路を作らないこと。"""
    body = _SRC.split("async def _notify", 1)[1].split("\n\n\n", 1)[0]
    assert '{"type": kind}' in body, "通知に中身を載せている"
