"""ユーザー管理APIルーター

Auth.js からのログイン upsert と管理者用ユーザー管理エンドポイント。
X-API-Key ヘッダーで認証（change_notify_api_key と共用）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..db.models import User, UserAccessGrant
from ..db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# 認証依存関数
# ---------------------------------------------------------------------------
def verify_api_key(x_api_key: Annotated[str, Header()] = "") -> None:
    """X-API-Key ヘッダーを検証する。"""
    if not settings.change_notify_api_key:
        if settings.api_env == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API key not configured",
            )
        return
    if x_api_key != settings.change_notify_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


ApiKeyDep = Annotated[None, Depends(verify_api_key)]
DbDep = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
# プレミアム判定ヘルパー
# ---------------------------------------------------------------------------
def get_user_premium_status(user_id: int, db: Session) -> tuple[bool, datetime | None]:
    """(is_premium, access_expires_at) を返す。access_expires_at=None は無期限。"""
    now = datetime.now(UTC)
    grants = (
        db.query(UserAccessGrant)
        .filter(
            UserAccessGrant.user_id == user_id,
            UserAccessGrant.is_active.is_(True),
            or_(
                UserAccessGrant.expires_at.is_(None),
                UserAccessGrant.expires_at > now,
            ),
        )
        .all()
    )
    if not grants:
        return False, None
    if any(g.expires_at is None for g in grants):
        return True, None
    access_expires_at = max(g.expires_at for g in grants if g.expires_at is not None)
    return True, access_expires_at


# ---------------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------------
class UpsertUserRequest(BaseModel):
    """ログイン時 upsert リクエスト"""

    google_sub: str
    email: str
    name: str | None = None
    image_url: str | None = None


class UserResponse(BaseModel):
    """ユーザーレスポンス"""

    id: int
    email: str
    name: str | None
    image_url: str | None
    role: str
    is_active: bool
    is_premium: bool
    access_expires_at: datetime | None
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": False}


class UpdateUserRequest(BaseModel):
    """ユーザー更新リクエスト（管理者用）"""

    role: str | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------
def _make_user_response(user: User, db: Session) -> UserResponse:
    """User ORM オブジェクトを UserResponse に変換する。"""
    is_premium, access_expires_at = get_user_premium_status(user.id, db)
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        image_url=user.image_url,
        role=user.role,
        is_active=user.is_active,
        is_premium=is_premium,
        access_expires_at=access_expires_at,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.post("/upsert", response_model=UserResponse)
def upsert_user(
    body: UpsertUserRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> UserResponse:
    """ログイン時にユーザーを upsert する。

    - 初回ログイン: 新規作成。admin_emails に含まれる場合 role=admin を付与。
    - 以降: last_login_at・name・image_url を更新。role は変更しない。
    """
    user = db.query(User).filter(User.google_sub == body.google_sub).first()
    now = datetime.now(UTC)

    if user is None:
        role = "admin" if body.email in settings.admin_email_list else "member"
        user = User(
            google_sub=body.google_sub,
            email=body.email,
            name=body.name,
            image_url=body.image_url,
            role=role,
            is_active=True,
            last_login_at=now,
        )
        db.add(user)
        logger.info("新規ユーザー登録: %s (role=%s)", body.email, role)
    else:
        user.name = body.name
        user.image_url = body.image_url
        user.last_login_at = now

    db.commit()
    db.refresh(user)
    return _make_user_response(user, db)


@admin_router.get("/users", response_model=list[UserResponse])
def list_users(
    _: ApiKeyDep,
    db: DbDep,
) -> list[UserResponse]:
    """全ユーザー一覧を返す（管理者用）。"""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [_make_user_response(u, db) for u in users]


@admin_router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    body: UpdateUserRequest,
    _: ApiKeyDep,
    db: DbDep,
) -> UserResponse:
    """ユーザーの role / is_active を更新する（管理者用）。"""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if body.role is not None:
        if body.role not in ("member", "admin"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="role は member または admin のみ有効",
            )
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active

    db.commit()
    db.refresh(user)
    logger.info("ユーザー更新: id=%d email=%s role=%s is_active=%s", user.id, user.email, user.role, user.is_active)
    return _make_user_response(user, db)
