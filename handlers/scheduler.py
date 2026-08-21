"""
Regular daily actions, per doc §5: 12:00 and 15:00 reminders to send a Reels
link for the evening story, 20:00 posts the story with a clickable link.
One job per account, all pinned to Europe/Moscow.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from sqlalchemy import select

from config import settings
from db.session import get_session
from db.models import Account, ChatBinding

scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)


async def _get_all_active_account_chat_pairs() -> list[tuple[int, int]]:
    """Every active account joined to its primary bound chat (see ChatBinding)."""
    async with get_session() as session:
        result = await session.execute(
            select(Account.id, ChatBinding.telegram_chat_id)
            .join(ChatBinding, ChatBinding.account_id == Account.id)
            .where(Account.status == "active", ChatBinding.is_primary.is_(True))
        )
        return [(account_id, chat_id) for account_id, chat_id in result.all()]


def register_daily_jobs(bot: Bot) -> None:
    async def send_reminder():
        for account_id, chat_id in await _get_all_active_account_chat_pairs():
            if chat_id:
                await bot.send_message(
                    chat_id, "Пришли ссылку на Reels для вечерней сторис."
                )

    async def publish_daily_story():
        for account_id, chat_id in await _get_all_active_account_chat_pairs():
            # TODO: pull the queued Reels link for this account and run the
            # HikerAPI story-with-link flow (doc §6.1)
            pass

    scheduler.add_job(send_reminder, CronTrigger(hour=12, minute=0))
    scheduler.add_job(send_reminder, CronTrigger(hour=15, minute=0))
    scheduler.add_job(publish_daily_story, CronTrigger(hour=20, minute=0))
