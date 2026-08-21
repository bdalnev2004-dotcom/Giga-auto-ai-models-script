from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config import settings
from db.models import Base

engine = create_async_engine(settings.DATABASE_URL, echo=False)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Run once on startup (or replace with Alembic migrations for production)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_session() -> AsyncSession:
    async with SessionFactory() as session:
        yield session
