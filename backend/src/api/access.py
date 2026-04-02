"""招待コード・アクセス付与 API

- POST /api/users/{user_id}/redeem-code  招待コードを使ってアクセス付与
- GET  /api/users/{user_id}/access       ユーザーのアクセス状態確認
- GET  /api/admin/invitation-codes       招待コード一覧（管理者）
- POST /api/admin/invitation-codes       招待コード作成（管理者）
- PATCH /api/admin/invitation-codes/{id} 招待コード更新（管理者）
- POST /api/admin/users/{user_id}/grant-access  直接アクセス付与（管理者）
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import InvitationCode, Race, User, UserAccessGrant
from .users import AdminRoleDep, ApiKeyDep, DbDep, get_user_premium_status

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

access_router = APIRouter(prefix="/api/users", tags=["access"])
access_admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _generate_code() -> str:
    """12桁の英数字コードを生成する（紛らわしい文字 O/0/I/1/L を除外）。"""
    alphabet = (string.ascii_uppercase + string.digits).translate(
        str.maketrans("", "", "O0I1L")
    )
    return "".join(secrets.choice(alphabet) for _ in range(12))


async def _calc_weeks_expiry(weeks_count: int, db: AsyncSession) -> datetime:
    """N 競馬週後の日曜 23:59:59 JST を返す。

    競馬週 = 少なくとも1レースが存在する月〜日の週。
    DB の races テーブルから将来の開催日を取得して集計する。
    """
    today_str = date_type.today().strftime("%Y%m%d")
    result = await db.execute(
        select(Race.date)
        .where(Race.date > today_str)
        .distinct()
        .order_by(Race.date)
    )
    rows = result.all()
    seen: set[tuple[int, int]] = set()
    sundays: list[date_type] = []
    for (ds,) in rows:
        d = datetime.strptime(ds, "%Y%m%d").date()
        key = d.isocalendar()[:2]  # (ISO year, ISO week number)
        if key not in seen:
            seen.add(key)
            # ISO week Sunday: weekday() は 0=Mon, 6=Sun
            sunday = d + timedelta(days=(6 - d.weekday()))
            sundays.append(sunday)

    if weeks_count <= len(sundays):
        target = sundays[weeks_count - 1]
    elif sundays:
        target = sundays[-1]
    else:
        # DB に将来レースなし → N 週後の日曜にフォールバック
        today = date_type.today()
        target = today + timedelta(weeks=weeks_count)
        target = target + timedelta(days=(6 - target.weekday()))

    dt_jst = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=JST)
    return dt_jst.astimezone(UTC)


def _calc_date_expiry(target_date: date_type) -> datetime:
    """指定日の 23:59:59 JST を UTC で返す。"""
    dt_jst = datetime(
        target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=JST
    )
    return dt_jst.astimezone(UTC)


# ---------------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------------

class RedeemCodeRequest(BaseModel):
    """招待コード利用リクエスト"""

    code: str


class AccessStatusResponse(BaseModel):
    """アクセス状態レスポンス"""

    user_id: int
    is_premium: bool
    access_expires_at: datetime | None


class InvitationCodeCreate(BaseModel):
    """招待コード作成リクエスト（管理者）"""

    grant_type: str  # unlimited / weeks / date
    weeks_count: int | None = None
    target_date: date_type | None = None
    max_uses: int = 1
    note: str | None = None


class InvitationCodeUpdate(BaseModel):
    """招待コード更新リクエスト（管理者）"""

    is_active: bool | None = None
    max_uses: int | None = None
    note: str | None = None


class InvitationCodeResponse(BaseModel):
    """招待コードレスポンス"""

    id: int
    code: str
    grant_type: str
    weeks_count: int | None
    target_date: date_type | None
    max_uses: int
    use_count: int
    is_active: bool
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class GrantAccessRequest(BaseModel):
    """直接アクセス付与リクエスト（管理者）"""

    grant_type: str  # unlimited / weeks / date
    weeks_count: int | None = None
    target_date: date_type | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# ユーザー向けエンドポイント
# ---------------------------------------------------------------------------

@access_router.post("/{user_id}/redeem-code", response_model=AccessStatusResponse)
async def redeem_code(
    user_id: int,
    body: RedeemCodeRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> AccessStatusResponse:
    """招待コードを使ってアクセスを付与する。"""
    code_str = body.code.upper().strip()
    code_result = await db.execute(
        select(InvitationCode).where(
            InvitationCode.code == code_str,
            InvitationCode.is_active.is_(True),
        )
    )
    code = code_result.scalar_one_or_none()
    if code is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="コードが見つかりません"
        )
    if code.use_count >= code.max_uses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="このコードは使用済みです"
        )

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if code.grant_type == "unlimited":
        expires_at = None
    elif code.grant_type == "weeks":
        expires_at = await _calc_weeks_expiry(code.weeks_count or 1, db)
    elif code.grant_type == "date":
        if code.target_date is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="target_date が設定されていません",
            )
        expires_at = _calc_date_expiry(code.target_date)
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="不正な grant_type",
        )

    grant = UserAccessGrant(
        user_id=user_id,
        grant_type=code.grant_type,
        expires_at=expires_at,
        source="code",
        source_code_id=code.id,
        note=code.note,
    )
    db.add(grant)
    code.use_count += 1
    await db.commit()
    logger.info(
        "コード使用: user_id=%d code=%s grant_type=%s expires_at=%s",
        user_id, code.code, code.grant_type, expires_at,
    )

    is_premium, access_expires_at = await get_user_premium_status(user_id, db)
    return AccessStatusResponse(
        user_id=user_id, is_premium=is_premium, access_expires_at=access_expires_at
    )


@access_router.get("/{user_id}/access", response_model=AccessStatusResponse)
async def get_access_status(
    user_id: int,
    _: ApiKeyDep,
    db: DbDep,
) -> AccessStatusResponse:
    """ユーザーのアクセス状態を返す。"""
    is_premium, access_expires_at = await get_user_premium_status(user_id, db)
    return AccessStatusResponse(
        user_id=user_id, is_premium=is_premium, access_expires_at=access_expires_at
    )


# ---------------------------------------------------------------------------
# 管理者向けエンドポイント
# ---------------------------------------------------------------------------

@access_admin_router.get("/invitation-codes", response_model=list[InvitationCodeResponse])
async def list_invitation_codes(
    _: ApiKeyDep,
    __: AdminRoleDep,
    db: DbDep,
) -> list[InvitationCode]:
    """招待コード一覧を返す（管理者）。"""
    result = await db.execute(
        select(InvitationCode).order_by(InvitationCode.created_at.desc())
    )
    return list(result.scalars().all())


@access_admin_router.post("/invitation-codes", response_model=InvitationCodeResponse)
async def create_invitation_code(
    body: InvitationCodeCreate,
    _: ApiKeyDep,
    __: AdminRoleDep,
    db: DbDep,
) -> InvitationCode:
    """招待コードを新規作成する（管理者）。"""
    if body.grant_type not in ("unlimited", "weeks", "date"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="grant_type は unlimited / weeks / date のみ有効",
        )
    if body.grant_type == "weeks" and not body.weeks_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="weeks タイプには weeks_count が必要です",
        )
    if body.grant_type == "date" and not body.target_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date タイプには target_date が必要です",
        )

    code = InvitationCode(
        code=_generate_code(),
        grant_type=body.grant_type,
        weeks_count=body.weeks_count,
        target_date=body.target_date,
        max_uses=body.max_uses,
        note=body.note,
    )
    db.add(code)
    await db.commit()
    await db.refresh(code)
    logger.info("招待コード作成: code=%s grant_type=%s", code.code, code.grant_type)
    return code


@access_admin_router.patch(
    "/invitation-codes/{code_id}", response_model=InvitationCodeResponse
)
async def update_invitation_code(
    code_id: int,
    body: InvitationCodeUpdate,
    _: ApiKeyDep,
    __: AdminRoleDep,
    db: DbDep,
) -> InvitationCode:
    """招待コードを更新する（管理者）。"""
    code = await db.get(InvitationCode, code_id)
    if code is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Code not found")
    if body.is_active is not None:
        code.is_active = body.is_active
    if body.max_uses is not None:
        code.max_uses = body.max_uses
    if body.note is not None:
        code.note = body.note
    await db.commit()
    await db.refresh(code)
    return code


@access_admin_router.post(
    "/users/{user_id}/grant-access", response_model=AccessStatusResponse
)
async def admin_grant_access(
    user_id: int,
    body: GrantAccessRequest,
    _: ApiKeyDep,
    __: AdminRoleDep,
    db: DbDep,
) -> AccessStatusResponse:
    """管理者が直接アクセスを付与する。"""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if body.grant_type == "unlimited":
        expires_at = None
    elif body.grant_type == "weeks":
        expires_at = await _calc_weeks_expiry(body.weeks_count or 1, db)
    elif body.grant_type == "date":
        if body.target_date is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date タイプには target_date が必要です",
            )
        expires_at = _calc_date_expiry(body.target_date)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="不正な grant_type"
        )

    grant = UserAccessGrant(
        user_id=user_id,
        grant_type=body.grant_type,
        expires_at=expires_at,
        source="admin",
        note=body.note,
    )
    db.add(grant)
    await db.commit()
    logger.info(
        "管理者アクセス付与: user_id=%d grant_type=%s expires_at=%s",
        user_id, body.grant_type, expires_at,
    )

    is_premium, access_expires_at = await get_user_premium_status(user_id, db)
    return AccessStatusResponse(
        user_id=user_id, is_premium=is_premium, access_expires_at=access_expires_at
    )
