import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from config import settings
from db.session import init_db
from handlers import account, scenarios, approvals
from handlers.scheduler import scheduler, register_daily_jobs

logging.basicConfig(level=logging.INFO)


def _build_storage():
    """
    FSM state (which question we're on, which account, pending job_id) must
    survive a process restart/redeploy — in-memory storage loses every active
    dialog the moment the container restarts. Redis is required in any real
    deployment; MemoryStorage stays as a local-dev fallback only.
    """
    if settings.REDIS_URL:
        return RedisStorage.from_url(settings.REDIS_URL)
    logging.warning("REDIS_URL not set — falling back to MemoryStorage (dev only, not for prod).")
    return MemoryStorage()


async def main() -> None:
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=_build_storage())

    # Order matters: account-switch and approval-state handlers should see
    # messages before the generic free-text trigger resolver.
    dp.include_router(account.router)
    dp.include_router(approvals.router)
    dp.include_router(scenarios.router)

    await init_db()
    register_daily_jobs(bot)
    scheduler.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
