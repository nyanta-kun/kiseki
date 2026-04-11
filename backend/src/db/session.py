"""データベースセッション管理"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from ..config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    connect_args={
        "timeout": 10,
        "server_settings": {"search_path": settings.db_schema},
    },
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# scripts等から使う同期エンジン（Alembic / バックテスト等）
sync_engine = create_engine(
    settings.database_url_sync,
    echo=settings.debug,
    pool_pre_ping=True,
)
SyncSessionLocal = sessionmaker(sync_engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    """SQLAlchemy ベースクラス（schema='keiba'）"""

    __table_args__ = {"schema": "keiba"}


async def get_db():
    """FastAPI Dependencyとして使用するDBセッション生成器"""
    async with AsyncSessionLocal() as session:
        yield session
