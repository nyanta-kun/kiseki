"""POG（ペーパーオーナーゲーム）の読み取り API。

統合 Phase 5 の 5d-3。sekito `backend/routes/pog.js`（2,668行・44 endpoint）の
うち、**実際に使われている 5 本**を移植する。

## なぜ 5 本なのか

nginx のアクセスログ 14 日ぶん（2026-08-25〜09-08）の実測で、`/api/pog/*` に
来たのは **44 本中 7 本だけ**。うち 4 本がほぼ全量だった:

    GET /owners-history              143
    GET /group/:id/recent-races      143
    GET /owners                      142
    GET /group/:id/user/:uid/horses  111
    GET /group/:id/user/all/horses     9
    GET /group/:id/sire-count          1
    GET /graded-wins                   1

🔴 **「残り 37 本は死んでいる」とは読めない。** ドラフト系（`/suggest`
`/roll` `/confirm`）は**年1回・春だけ**使われ、この 14 日はオフシーズン。
次のドラフトは 2027年春。判断材料にしてよいのは**移植の順序**であって、
削除の可否ではない（docs/pog_migration_plan_2026_09_08.md §5.1）。

## 移設元との違い

- 成績は `sekito.horse` の保存列ではなく `keiba.horse_runs` から**毎回数える**。
  保存列は誰も更新しておらず、2024年産は全頭 0 だった（services/pog_standings.py）
- 馬 ID の解決に `sekito.entries` を使わない。あちらは **2026-05-03 で凍結**
  しており、`v_entries.netkeiba_horse_id` は 2026-06 以降 **0%**
- 認証は付けない。移設元も POG の 44 本中 `requireAuth` は **0 本**で、
  読み取りは公開だった（匿名 curl で順位表が取れることを確認済み）。
  ⚠️ 画面側は Auth.js のログインが要る（proxy.ts）。ここを変えるなら
  「読み取りを公開のままにするか」の判断（Phase 4 の案 B）が要る
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_db
from ..services.pog_standings import fetch_horses, fetch_owners

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pog", tags=["pog"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


class GroupOut(BaseModel):
    """POG のグループ（1年 = 1グループ）。"""

    id: int
    year: int
    name: str | None


class OwnerOut(BaseModel):
    """馬主別の成績。"""

    rank: int
    user_id: int
    name: str | None
    win: int
    place: int
    show: int
    out: int
    prize: int
    prize_jra: int
    prize_other: int
    top_horse: str | None


class OwnerRankOut(BaseModel):
    """順位だけ（変動矢印の計算に使う）。"""

    rank: int
    user_id: int


async def _group_id_for_year(db: AsyncSession, year: int) -> int:
    """年度からグループ id を引く。

    🔴 **外向きの指定は year にする。** 移設元の `group_id` は keiba へ写した
    時点で別体系になり（2026 = sekito 27 / keiba 22）、しかも sekito 側は
    年順ですらなかった（2025=3・2024=6）。ID を跨いで使うと必ず取り違える。
    year は一意（`uq_pog_groups_year`）で人が読んで分かる。
    """
    row = await db.execute(
        text("SELECT id FROM keiba.pog_groups WHERE year = :year"), {"year": year}
    )
    found = row.scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail=f"{year} 年度のグループがありません")
    return found


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(db: DbDep) -> list[GroupOut]:
    """グループを新しい年度から返す。"""
    rows = await db.execute(
        text("SELECT id, year, name FROM keiba.pog_groups ORDER BY year DESC")
    )
    return [GroupOut(id=r.id, year=r.year, name=r.name) for r in rows]


@router.get("/owners", response_model=list[OwnerOut])
async def get_owners(
    db: DbDep, year: int = Query(..., description="POG の年度（例 2026）")
) -> list[OwnerOut]:
    """馬主別の成績を賞金の降順で返す。"""
    group_id = await _group_id_for_year(db, year)
    return [OwnerOut(**vars(o)) for o in await fetch_owners(db, group_id)]


@router.get("/owners-history", response_model=list[OwnerRankOut])
async def get_owners_history(
    db: DbDep,
    year: int = Query(..., description="POG の年度（例 2026）"),
    asof: date = Query(..., description="この日までの成績で順位を出す"),
) -> list[OwnerRankOut]:
    """指定日時点の順位を返す（順位変動の矢印に使う）。

    🔴 `/owners` と**同じ集計**に日付フィルタを足しただけにする。
    別のクエリで出すと、変動していないのに矢印が出る（移設元のコメントに
    その修正履歴が残っている）。
    """
    group_id = await _group_id_for_year(db, year)
    owners = await fetch_owners(db, group_id, asof=asof)
    return [OwnerRankOut(rank=o.rank, user_id=o.user_id) for o in owners]


@router.get("/horses")
async def get_group_horses(
    db: DbDep,
    year: int = Query(..., description="POG の年度（例 2026）"),
    user_id: int | None = Query(None, description="省略するとグループ全馬"),
) -> list[dict]:
    """指名馬の一覧を返す。

    移設元の `/group/:id/user/:uid/horses` と `/group/:id/user/all/horses` を
    1 本にまとめた（違いは絞り込みだけだった）。
    """
    group_id = await _group_id_for_year(db, year)
    return await fetch_horses(db, group_id, user_id=user_id)
