from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_db_or_none() -> AsyncIterator[AsyncSession | None]:
    """DB가 없어도 라우트가 동작하도록 None을 반환하는 optional 의존성."""
    try:
        async with SessionLocal() as session:
            # 실제 연결 가능한지 확인
            await session.execute(text("SELECT 1"))
            yield session
    except Exception:
        yield None
