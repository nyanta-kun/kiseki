"""データベースセッション管理"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

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


class Base(DeclarativeBase):
    """SQLAlchemy ベースクラス（schema='keiba'）"""

    __table_args__ = {"schema": "keiba"}


async def get_db():
    """FastAPI Dependencyとして使用するDBセッション生成器"""
    async with AsyncSessionLocal() as session:
        yield session
