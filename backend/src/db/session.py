"""データベースセッション管理"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from ..config import settings

engine = create_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy ベースクラス（schema='keiba'）"""

    __table_args__ = {"schema": "keiba"}


def get_db():
    """FastAPI Dependencyとして使用するDBセッション生成器"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
