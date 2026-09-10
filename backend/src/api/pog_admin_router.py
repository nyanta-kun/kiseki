"""POG のグループ管理（sekito `/api/pog-groups` の移設先）。

統合 Phase 5 の 5d-5。**POG で唯一の書き込み API**（ドラフトを除く）。
年度グループの作成・削除と、メンバーの追加・削除。

## 認証

`X-API-Key`（`users.py` の `verify_api_key` と同じ鍵）。**ブラウザから直接
叩かせない。** フロントの route handler が Auth.js のセッションで
`role === "admin"` を確かめてから、サーバ側だけが持つ鍵で呼ぶ
（`frontend/src/app/api/pog/groups/route.ts`。`/api/admin/settings` と同じ形）。

移設元も `requireAdmin` で管理者限定だった。

## 🔴 削除は指名ごと消える

グループを消すと `pog_picks` が **ON DELETE CASCADE で道連れ**になる
（移設元も明示的に DELETE していた）。1 年ぶんの指名が丸ごと消えるので、
**本文に消したい年度を書かせて一致を確認する**（`confirm_year`）。
移設元にこの確認は無く、id を押し間違えたら戻せなかった。

⚠️ メンバー（`pog_group_members`）も CASCADE で消える。

## user_ids は持ち込まない

移設元は `pog_group.user_ids` に **text のカンマ区切り**
（`"3,4,6,8"`）でメンバーを持っていた。参照整合性が効かないので
`pog_group_members` へ正規化してある（`db/models.py` の docstring 参照）。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from .users import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pog/admin", tags=["pog-admin"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
ApiKeyDep = Annotated[None, Depends(verify_api_key)]


class MemberIn(BaseModel):
    """グループに入れる 1 人。"""

    user_id: int
    #: 表示名。省略すると `keiba.users.name` が使われる。
    nickname: str | None = None


class GroupIn(BaseModel):
    """作成するグループ。"""

    year: int = Field(..., ge=2000, le=2100)
    name: str | None = None
    members: list[MemberIn] = Field(default_factory=list)


class MemberOut(BaseModel):
    """グループのメンバー 1 人。"""

    user_id: int
    nickname: str | None
    name: str | None
    email: str | None
    #: そのメンバーの指名数（`pick_order <> 0`）。削除の判断材料に出す。
    pick_count: int


class GroupDetailOut(BaseModel):
    """グループ 1 件。"""

    id: int
    year: int
    name: str | None
    members: list[MemberOut]
    pick_count: int


async def _group_by_year(db: AsyncSession, year: int) -> int:
    row = await db.execute(
        text("SELECT id FROM keiba.pog_groups WHERE year = :y"), {"y": year}
    )
    found = row.scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail=f"{year} 年度のグループがありません")
    return found


@router.get("/groups/{year}", response_model=GroupDetailOut)
async def get_group(db: DbDep, _: ApiKeyDep, year: int) -> GroupDetailOut:
    """グループとメンバーを返す。"""
    group_id = await _group_by_year(db, year)
    rows = await db.execute(
        text(
            """
            SELECT m.user_id, m.nickname, u.name, u.email,
                   (SELECT count(*) FROM keiba.pog_picks p
                     WHERE p.group_id = m.group_id AND p.user_id = m.user_id
                       AND p.pick_order <> 0)::int AS pick_count
            FROM keiba.pog_group_members m
            LEFT JOIN keiba.users u ON u.id = m.user_id
            WHERE m.group_id = :gid
            ORDER BY COALESCE(m.nickname, u.name)
            """
        ),
        {"gid": group_id},
    )
    members = [MemberOut(**dict(r)) for r in rows.mappings()]
    total = await db.execute(
        text(
            "SELECT count(*)::int FROM keiba.pog_picks "
            "WHERE group_id = :gid AND pick_order <> 0"
        ),
        {"gid": group_id},
    )
    return GroupDetailOut(
        id=group_id,
        year=year,
        name=(
            await db.execute(
                text("SELECT name FROM keiba.pog_groups WHERE id = :gid"),
                {"gid": group_id},
            )
        ).scalar_one_or_none(),
        members=members,
        pick_count=total.scalar_one(),
    )


@router.post("/groups", response_model=GroupDetailOut, status_code=status.HTTP_201_CREATED)
async def create_group(db: DbDep, _: ApiKeyDep, body: GroupIn) -> GroupDetailOut:
    """年度グループを作る。

    ⚠️ 年度は一意（`uq_pog_groups_year`）。同じ年を 2 度作ろうとすると 409。
    """
    exists = await db.execute(
        text("SELECT 1 FROM keiba.pog_groups WHERE year = :y"), {"y": body.year}
    )
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{body.year} 年度のグループは既にあります",
        )
    row = await db.execute(
        text(
            "INSERT INTO keiba.pog_groups (year, name) VALUES (:y, :n) RETURNING id"
        ),
        {"y": body.year, "n": body.name},
    )
    group_id = row.scalar_one()
    for m in body.members:
        await db.execute(
            text(
                "INSERT INTO keiba.pog_group_members (group_id, user_id, nickname) "
                "VALUES (:g, :u, :n) ON CONFLICT DO NOTHING"
            ),
            {"g": group_id, "u": m.user_id, "n": m.nickname},
        )
    await db.commit()
    logger.info("POG グループを作成: year=%s members=%d", body.year, len(body.members))
    return await get_group(db, None, body.year)


class MembersIn(BaseModel):
    """メンバーの置き換え。"""

    members: list[MemberIn]


@router.put("/groups/{year}/members", response_model=GroupDetailOut)
async def replace_members(
    db: DbDep, _: ApiKeyDep, year: int, body: MembersIn
) -> GroupDetailOut:
    """メンバーを丸ごと置き換える。

    🔴 **外れたメンバーの指名は消さない。** `pog_picks` はそのまま残す
    （メンバー表から外れても過去の成績は記録として要る）。消したい場合は
    グループごと削除すること。
    """
    group_id = await _group_by_year(db, year)
    keep = [m.user_id for m in body.members]
    await db.execute(
        text(
            "DELETE FROM keiba.pog_group_members "
            "WHERE group_id = :g AND NOT (user_id = ANY(:keep))"
        ),
        {"g": group_id, "keep": keep or [-1]},
    )
    for m in body.members:
        await db.execute(
            text(
                "INSERT INTO keiba.pog_group_members (group_id, user_id, nickname) "
                "VALUES (:g, :u, :n) "
                "ON CONFLICT (group_id, user_id) DO UPDATE SET nickname = EXCLUDED.nickname"
            ),
            {"g": group_id, "u": m.user_id, "n": m.nickname},
        )
    await db.commit()
    logger.info("POG メンバーを更新: year=%s members=%d", year, len(body.members))
    return await get_group(db, None, year)


class DeleteIn(BaseModel):
    """削除の確認。"""

    #: 消したい年度をもう一度書かせる。`year` と一致しなければ拒否する。
    confirm_year: int


class DeleteOut(BaseModel):
    """削除の結果。"""

    year: int
    deleted_picks: int
    deleted_members: int
    deleted_rolls: int


@router.post("/groups/{year}/delete", response_model=DeleteOut)
async def delete_group(
    db: DbDep, _: ApiKeyDep, year: int, body: DeleteIn
) -> DeleteOut:
    """年度グループを消す。**指名も一緒に消える。**

    🔴 `pog_picks` と `pog_group_members` は `ON DELETE CASCADE` で道連れになる。
    1 年ぶんの指名が丸ごと消えるので、`confirm_year` に同じ年度を書かせて
    一致を確認する。移設元にこの確認は無く、押し間違えたら戻せなかった。

    ⚠️ DELETE ではなく POST にしてあるのは、確認の本文を送らせるため。
    """
    if body.confirm_year != year:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"確認の年度が一致しません（{year} を消すなら confirm_year も {year}）",
        )
    group_id = await _group_by_year(db, year)
    picks = (
        await db.execute(
            text("SELECT count(*)::int FROM keiba.pog_picks WHERE group_id = :g"),
            {"g": group_id},
        )
    ).scalar_one()
    members = (
        await db.execute(
            text(
                "SELECT count(*)::int FROM keiba.pog_group_members WHERE group_id = :g"
            ),
            {"g": group_id},
        )
    ).scalar_one()
    # 🔴 `pog_rolls` は年度をキーに持ち `pog_groups` への FK が無いので
    #    CASCADE で消えない。明示的に消す（2026-09-10 の実地検証で
    #    グループを消した後に出目だけ残るのを確認した）。
    rolls = (
        await db.execute(
            text("DELETE FROM keiba.pog_rolls WHERE year = :y"), {"y": year}
        )
    ).rowcount  # type: ignore[attr-defined]
    await db.execute(
        text("DELETE FROM keiba.pog_groups WHERE id = :g"), {"g": group_id}
    )
    await db.commit()
    # 🔴 消したことは必ず残す（戻せない操作なので）。
    logger.warning(
        "POG グループを削除: year=%s picks=%d members=%d rolls=%d",
        year, picks, members, rolls,
    )
    return DeleteOut(
        year=year, deleted_picks=picks, deleted_members=members, deleted_rolls=rolls
    )
