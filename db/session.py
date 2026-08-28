from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from config import settings
from db.models import Base

engine = create_async_engine(settings.DATABASE_URL, echo=False)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """
    Local-dev convenience only — creates whatever tables are missing.

    create_all() only ever CREATEs; it never ALTERs an existing table, so on a
    database that already has data this silently does nothing for a changed or
    new column (persona_json, anchor_image_url and chat_contexts all shipped
    this way at first, invisibly, until the schema was rebuilt from scratch).
    Fine for the empty SQLite file a fresh local run starts from — not fine once
    the farm has real rows. From there on, use Alembic:

        alembic upgrade head              # apply pending migrations
        alembic revision --autogenerate -m "describe the change"  # after editing db/models.py

    bot.py still calls this on every startup; it is a no-op once the schema is
    current, so leaving it in does not conflict with also running migrations.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_session() -> AsyncSession:
    async with SessionFactory() as session:
        yield session
