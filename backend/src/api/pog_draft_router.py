"""POG ドラフト（sekito `/api/pog/user` `/roll` `/confirm` の移設先）。

統合 Phase 5 の 5e-2。**年 1 回・春だけ**動く機能で、次のドラフトは 2027年春。
つまり**正しさを実地で確かめられるのは半年以上先**なので、規則は移設元から
そのまま写し、判定に効く部分はテストで固定する。

## 規則（実データから確認したもの）

    draft_order   指名の巡（1巡目、2巡目…）
    pick_order    その人のチーム内の枠番。**0 = その巡で取れなかった**
    visible       伏せているか。管理者が巡ごとに一斉公開する

同じ巡に複数人が**同じ馬**を指名したら、サイコロ 3 個で勝者を決める。
勝者に `pick_order` が入り、敗者は `0` になって**次の巡で指名し直す**。

2026年度の 1 巡目の実データがそのまま例になっている:

    draft_order=1  7 人が指名。うち 3 人が「ジョドレルバンク」
                   → 松が pick_order=1、渡とフクシゲが 0
    draft_order=2  **その 2 人だけ**が再指名。両者 pick_order=1

## 🔴 確定は管理者が押す

移設元の `POST /confirm` は「その巡の指名が 1 件だけなら自動確定」だが、
実際に使われているのは**管理者が画面のセルを押す** `PUT /user/order` の方
（`PogDraftPage.tsx` 1,261 行）。ここでは両方を移すが、**正本は
`PUT /order`**（勝者に番号、他を 0）。

## 認証

バックエンドは `X-API-Key` しか見ない。**誰が誰として指名したかを確かめるのは
フロントの server action だけ**（`/admin` の POG タブと同じ形）。

- 自分の指名 … `user_id` がセッションの利用者と一致すること
- 代理入力 …… 呼び出し側が `role === "admin"` であること
- 管理操作 …… 同上

⚠️ この層を飛ばす経路を作らないこと。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from .users import verify_api_key
from .ws_manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pog/draft", tags=["pog-draft"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
ApiKeyDep = Annotated[None, Depends(verify_api_key)]

#: ドラフト画面のリアルタイム更新。**年度をキーにする**（移設元は socket.io の
#: `group_{id}` ルーム）。バックエンドは uvicorn 1 プロセスなので在メモリで足りる。
#: ⚠️ ワーカーを増やすならここが分断される（`main.py` の lifespan の注意も参照）。
draft_manager = ConnectionManager()


async def _group_id(db: AsyncSession, year: int) -> int:
    row = await db.execute(
        text("SELECT id FROM keiba.pog_groups WHERE year = :y"), {"y": year}
    )
    found = row.scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail=f"{year} 年度のグループがありません")
    return found


async def _notify(year: int, kind: str) -> None:
    """ドラフト画面へ「変わった」とだけ伝える。

    中身は送らない（受け取った側が読み直す）。**送る内容を増やすと、
    伏せている指名が公開前に漏れる**経路になりうる。
    """
    await draft_manager.broadcast(year, {"type": kind})


# --------------------------------------------------------------------- 読み取り


class DraftPickOut(BaseModel):
    """指名 1 件。"""

    user_id: int
    owner_name: str | None
    draft_order: int
    pick_order: int | None
    visible: bool
    netkeiba_horse_id: str | None
    horse_name: str | None
    sex: str | None
    sire: str | None
    broodmare: str | None
    stable: str | None


class DraftOut(BaseModel):
    """ドラフトの盤面。"""

    year: int
    members: list[dict]
    picks: list[DraftPickOut]
    max_draft_order: int


@router.get("", response_model=DraftOut)
async def get_draft(
    db: DbDep,
    _: ApiKeyDep,
    year: int = Query(..., description="POG の年度"),
    viewer_user_id: int | None = Query(None, description="見ている人。自分の伏せ札は見える"),
    is_admin: bool = Query(False, description="管理者なら伏せ札も全部見える"),
) -> DraftOut:
    """盤面を返す。

    🔴 **伏せている指名を出し分ける。** 管理者と本人以外には `visible = true`
    の行しか返さない。移設元と同じだが、あちらは「本人ぶんを取る」ときに
    フィルタ自体を外していた（`uid` 指定なら visible を見ない）。ここでは
    **1 本のクエリで本人だけ例外にする**（呼び分けを間違えて全部見えるのを防ぐ）。
    """
    group_id = await _group_id(db, year)
    rows = await db.execute(
        text(
            """
            SELECT
                p.user_id,
                COALESCE(m.nickname, u.name) AS owner_name,
                p.draft_order,
                p.pick_order,
                p.visible,
                p.netkeiba_horse_id,
                h.name AS horse_name,
                h.sex, h.sire, h.broodmare, h.stable
            FROM keiba.pog_picks p
            LEFT JOIN keiba.pog_group_members m
                   ON m.group_id = p.group_id AND m.user_id = p.user_id
            LEFT JOIN keiba.users u ON u.id = p.user_id
            LEFT JOIN keiba.pog_horses h
                   ON h.netkeiba_horse_id = p.netkeiba_horse_id
            WHERE p.group_id = :gid
              AND (p.visible OR :is_admin OR p.user_id = :viewer)
            ORDER BY p.draft_order, owner_name
            """
        ),
        {"gid": group_id, "is_admin": is_admin, "viewer": viewer_user_id or -1},
    )
    picks = [DraftPickOut(**dict(r)) for r in rows.mappings()]
    members = await db.execute(
        text(
            "SELECT m.user_id, COALESCE(m.nickname, u.name) AS name "
            "FROM keiba.pog_group_members m "
            "LEFT JOIN keiba.users u ON u.id = m.user_id "
            "WHERE m.group_id = :gid ORDER BY name"
        ),
        {"gid": group_id},
    )
    return DraftOut(
        year=year,
        members=[dict(r) for r in members.mappings()],
        picks=picks,
        max_draft_order=max((p.draft_order for p in picks), default=0),
    )


class RollOut(BaseModel):
    """サイコロの出目 1 人ぶん。"""

    user_id: int
    die1: int
    die2: int
    die3: int
    sum: int


@router.get("/rolls", response_model=list[RollOut])
async def get_rolls(
    db: DbDep,
    _: ApiKeyDep,
    year: int = Query(...),
    draft_order: int = Query(...),
) -> list[RollOut]:
    """その巡のサイコロ結果を返す。"""
    rows = await db.execute(
        text(
            'SELECT user_id, die1, die2, die3, "sum" FROM keiba.pog_rolls '
            "WHERE year = :y AND draft_order = :d ORDER BY \"sum\" DESC, user_id"
        ),
        {"y": year, "d": draft_order},
    )
    return [RollOut(**dict(r)) for r in rows.mappings()]


# --------------------------------------------------------------------- 書き込み


class PickIn(BaseModel):
    """指名 1 件の登録。"""

    user_id: int
    draft_order: int = Field(..., ge=1)
    netkeiba_horse_id: str


@router.post("/picks", response_model=DraftPickOut)
async def save_pick(db: DbDep, _: ApiKeyDep, year: int, body: PickIn) -> DraftPickOut:
    """指名を保存する（同じ人・同じ巡なら上書き）。

    ⚠️ 新しい指名は **`visible = false`（伏せ）で入る**。管理者が巡ごとに
    一斉公開するまで他人には見えない。ここを true にすると、
    **先に入れた人の指名を見てから指名できる**ようになる。
    """
    group_id = await _group_id(db, year)
    await db.execute(
        text(
            """
            INSERT INTO keiba.pog_picks
                (group_id, user_id, draft_order, netkeiba_horse_id, visible)
            VALUES (:g, :u, :d, :h, false)
            ON CONFLICT (group_id, user_id, draft_order)
            DO UPDATE SET netkeiba_horse_id = EXCLUDED.netkeiba_horse_id,
                          updated_at = now()
            """
        ),
        {"g": group_id, "u": body.user_id, "d": body.draft_order, "h": body.netkeiba_horse_id},
    )
    await db.commit()
    await _notify(year, "pick")
    out = await get_draft(db, None, year=year, viewer_user_id=body.user_id, is_admin=True)
    for p in out.picks:
        if p.user_id == body.user_id and p.draft_order == body.draft_order:
            return p
    raise HTTPException(status_code=500, detail="保存した指名を読み戻せませんでした")


@router.delete("/picks")
async def delete_pick(
    db: DbDep,
    _: ApiKeyDep,
    year: int = Query(...),
    user_id: int = Query(...),
    draft_order: int = Query(...),
) -> dict:
    """指名を取り消す。"""
    group_id = await _group_id(db, year)
    res = await db.execute(
        text(
            "DELETE FROM keiba.pog_picks "
            "WHERE group_id = :g AND user_id = :u AND draft_order = :d"
        ),
        {"g": group_id, "u": user_id, "d": draft_order},
    )
    await db.commit()
    await _notify(year, "pick")
    # `Result` の型定義に rowcount が無い（DML では実際には返る）。
    return {"deleted": res.rowcount}  # type: ignore[attr-defined]


class VisibleIn(BaseModel):
    """その巡を公開するか。"""

    draft_order: int
    visible: bool


@router.put("/visible")
async def set_visible(db: DbDep, _: ApiKeyDep, year: int, body: VisibleIn) -> dict:
    """その巡の指名をまとめて公開／非公開にする（管理者）。"""
    group_id = await _group_id(db, year)
    res = await db.execute(
        text(
            "UPDATE keiba.pog_picks SET visible = :v, updated_at = now() "
            "WHERE group_id = :g AND draft_order = :d"
        ),
        {"v": body.visible, "g": group_id, "d": body.draft_order},
    )
    await db.commit()
    await _notify(year, "visible")
    return {"updated": res.rowcount}  # type: ignore[attr-defined]


class OrderTarget(BaseModel):
    """1 人ぶんの確定内容。"""

    user_id: int
    #: その人のチーム内の枠番。**0 = その巡は取れなかった**。
    pick_order: int


class OrderIn(BaseModel):
    """その巡の確定。"""

    draft_order: int
    targets: list[OrderTarget]


@router.put("/order")
async def set_order(db: DbDep, _: ApiKeyDep, year: int, body: OrderIn) -> dict:
    """その巡を確定する（管理者）。

    🔴 **これがドラフトの正本。** 勝った人に枠番を入れ、同じ馬を指名して
    負けた人を `0` にする。`0` の人は次の巡で指名し直す。

    ⚠️ 送られてこなかった人は触らない（`undefined` で上書きして
    確定済みを消さないため。移設元も `if (!('order' in target)) continue`
    で同じことをしていた）。
    """
    group_id = await _group_id(db, year)
    for t in body.targets:
        await db.execute(
            text(
                'UPDATE keiba.pog_picks SET pick_order = :o, updated_at = now() '
                "WHERE group_id = :g AND user_id = :u AND draft_order = :d"
            ),
            {"o": t.pick_order, "g": group_id, "u": t.user_id, "d": body.draft_order},
        )
    await db.commit()
    await _notify(year, "order")
    logger.info(
        "POG ドラフト確定: year=%s draft_order=%s targets=%d",
        year, body.draft_order, len(body.targets),
    )
    return {"updated": len(body.targets)}


class SkipIn(BaseModel):
    """その巡を飛ばす人。"""

    draft_order: int
    user_id: int
    skip: bool = True


@router.put("/skip")
async def set_skip(db: DbDep, _: ApiKeyDep, year: int, body: SkipIn) -> dict:
    """未入力の人を「不参加」にして巡を進められるようにする（管理者）。

    馬 ID 無し・`pick_order = 0` の行を作る。`skip=false` でその行だけ消して
    未入力へ戻す（**馬を指名済みの行は消さない**）。
    """
    group_id = await _group_id(db, year)
    if body.skip:
        await db.execute(
            text(
                """
                INSERT INTO keiba.pog_picks
                    (group_id, user_id, draft_order, netkeiba_horse_id, pick_order, visible)
                VALUES (:g, :u, :d, NULL, 0, true)
                ON CONFLICT (group_id, user_id, draft_order)
                DO UPDATE SET pick_order = 0, netkeiba_horse_id = NULL,
                              visible = true, updated_at = now()
                """
            ),
            {"g": group_id, "u": body.user_id, "d": body.draft_order},
        )
    else:
        await db.execute(
            text(
                "DELETE FROM keiba.pog_picks "
                "WHERE group_id = :g AND user_id = :u AND draft_order = :d "
                "  AND netkeiba_horse_id IS NULL"
            ),
            {"g": group_id, "u": body.user_id, "d": body.draft_order},
        )
    await db.commit()
    await _notify(year, "skip")
    return {"ok": True}


class RollIn(BaseModel):
    """サイコロの出目。"""

    user_id: int
    draft_order: int
    die1: int = Field(..., ge=1, le=6)
    die2: int = Field(..., ge=1, le=6)
    die3: int = Field(..., ge=1, le=6)


@router.post("/rolls", response_model=RollOut)
async def save_roll(db: DbDep, _: ApiKeyDep, year: int, body: RollIn) -> RollOut:
    """サイコロの出目を保存する（振り直しは上書き）。

    ⚠️ 出目は 1〜6 に限る（DB 側にも CHECK がある）。移設元は無検査で、
    クライアントが送った値をそのまま保存していた。
    """
    total = body.die1 + body.die2 + body.die3
    await db.execute(
        text(
            """
            INSERT INTO keiba.pog_rolls (year, user_id, draft_order, die1, die2, die3, "sum")
            VALUES (:y, :u, :d, :d1, :d2, :d3, :s)
            ON CONFLICT (year, user_id, draft_order)
            DO UPDATE SET die1 = EXCLUDED.die1, die2 = EXCLUDED.die2,
                          die3 = EXCLUDED.die3, "sum" = EXCLUDED."sum"
            """
        ),
        {
            "y": year, "u": body.user_id, "d": body.draft_order,
            "d1": body.die1, "d2": body.die2, "d3": body.die3, "s": total,
        },
    )
    await db.commit()
    await _notify(year, "roll")
    return RollOut(
        user_id=body.user_id, die1=body.die1, die2=body.die2, die3=body.die3, sum=total
    )


@router.websocket("/ws")
async def draft_ws(ws: WebSocket, year: int) -> None:
    """ドラフト画面のリアルタイム更新。

    「変わった」とだけ送る。中身は受け取った側が読み直す
    （伏せ札の出し分けは読み取り API が担うので、ここで内容を送らない）。
    """
    await draft_manager.connect(year, ws)
    try:
        while True:
            await ws.receive_text()  # クライアントからの ping を待つだけ
    except WebSocketDisconnect:
        draft_manager.disconnect(year, ws)
