"""POG グループ管理の安全装置を固定する。

なぜ必要か（2026-09-10・統合 Phase 5）:
    🔴 グループを消すと **その年の指名が道連れで消える**（`ON DELETE CASCADE`）。
    戻せない操作なので、確認の仕組みと認証の配線が外れていないことを
    機械的に留める。移設元にはこの確認が無く、id を押し間違えたら
    1 年ぶんの指名が失われた。
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.api import pog_admin_router as m


def test_書き込みは全て認証を要求する():
    """`ApiKeyDep` が付いていない書き込みエンドポイントを作らないこと。"""
    src = Path(m.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    unguarded: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        is_route = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and getattr(d.func.value, "id", None) == "router"
            for d in node.decorator_list
        )
        if not is_route:
            continue
        annotations = {
            getattr(a.annotation, "id", None)
            for a in node.args.args
            if a.annotation is not None
        }
        if "ApiKeyDep" not in annotations:
            unguarded.append(node.name)
    assert not unguarded, f"認証が付いていないエンドポイント: {unguarded}"


def test_削除は年度の一致を確認する():
    """`confirm_year` が `year` と一致しなければ落とすこと。"""
    src = Path(m.__file__).read_text(encoding="utf-8")
    body = src.split("async def delete_group", 1)[1]
    assert "body.confirm_year != year" in body, "確認の比較が無い"
    # 比較より前に DELETE を撃っていないこと。
    check_at = body.index("body.confirm_year != year")
    delete_at = body.index("DELETE FROM keiba.pog_groups")
    assert check_at < delete_at, "確認より先に削除している"


def test_削除は_POST_で受ける():
    """本文（確認の年度）を送らせるため。DELETE メソッドでは本文が落ちうる。"""
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert '@router.post("/groups/{year}/delete"' in src
    assert "@router.delete(" not in src, "本文を伴わない削除経路を作らないこと"


def test_削除は必ずログに残す():
    """戻せない操作なので、何が消えたかを残す。"""
    src = Path(m.__file__).read_text(encoding="utf-8")
    body = src.split("async def delete_group", 1)[1]
    assert "logger.warning" in body, "削除の記録が無い"


def test_メンバー入れ替えは指名を消さない():
    """外れたメンバーの `pog_picks` は残す（過去の記録として要る）。"""
    src = Path(m.__file__).read_text(encoding="utf-8")
    body = src.split("async def replace_members", 1)[1].split("class DeleteIn", 1)[0]
    assert "DELETE FROM keiba.pog_group_members" in body
    # docstring では `pog_picks` に触れてよいので、DELETE 文だけを見る。
    assert "DELETE FROM keiba.pog_picks" not in body, (
        "メンバー入れ替えで指名を消してはいけない"
    )


def test_フロントは管理者だけに通す():
    """server action 側で role を確かめていること。

    バックエンドは `X-API-Key` しか見ないので、**管理者かどうかを
    確かめているのはフロントだけ**。ここが外れると鍵を持つ経路が
    誰にでも開く。
    """
    p = Path("../frontend/src/app/admin/pogActions.ts")
    src = p.read_text(encoding="utf-8")
    assert 'role === "admin"' in src, "role の確認が無い"
    for fn in ("createPogGroup", "replacePogMembers", "deletePogGroup", "getPogGroup"):
        body = src.split(f"export async function {fn}", 1)[1].split("\n}", 1)[0]
        assert "assertAdmin()" in body, f"{fn} が role を確かめていない"


def test_グループ削除でサイコロも消す():
    """`pog_rolls` は年度キーで `pog_groups` への FK が無く CASCADE で消えない。

    2026-09-10 の実地検証（ダミーの 2099 年度）で、グループを消した後に
    出目だけが残るのを確認した。残しても害は小さいが、同じ年度を作り直すと
    前回の出目が見えてしまう。
    """
    src = Path(m.__file__).read_text(encoding="utf-8")
    body = src.split("async def delete_group", 1)[1]
    assert "DELETE FROM keiba.pog_rolls" in body, "サイコロが消えずに残る"
    assert body.index("DELETE FROM keiba.pog_rolls") < body.index(
        "DELETE FROM keiba.pog_groups"
    ), "グループを先に消すと年度が引けなくなる"
