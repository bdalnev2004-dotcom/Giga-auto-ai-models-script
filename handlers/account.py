"""
'/account N' (or 'аккаунт N' / 'переключись на N') — pulls up that account's card
and routes every subsequent command to it until switched again. Required before
any scenario if the chat manages more than one account (doc §2).
"""
import re

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from db.session import get_session
from db.models import Account, ChatBinding

router = Router(name="account")

# in-memory per-chat "current account" cache; FSMContext storage is the durable copy
_current_account_cache: dict[int, int] = {}


def get_current_account_id(chat_id: int) -> int | None:
    return _current_account_cache.get(chat_id)


async def bind_current_account(chat_id: int, account_id: int) -> None:
    """
    Switches this chat's active account AND persists a ChatBinding row, so the
    scheduler (handlers/scheduler.py) knows where to send 12:00/15:00/20:00
    messages for this account. Called right after create_brand/create_blogger
    finishes, and can be called again any time to (re)bind an existing account
    to a new chat (e.g. an agency group).
    """
    _current_account_cache[chat_id] = account_id

    async with get_session() as session:
        result = await session.execute(
            select(ChatBinding).where(
                ChatBinding.account_id == account_id,
                ChatBinding.telegram_chat_id == chat_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            session.add(
                ChatBinding(account_id=account_id, telegram_chat_id=chat_id, is_primary=True)
            )
            await session.commit()


@router.message(F.text.regexp(r"(?i)^(/account|аккаунт|переключись на)\s+(\d+)"))
async def switch_account(message: Message, state: FSMContext):
    match = re.search(r"(\d+)", message.text)
    account_number = int(match.group(1))

    async with get_session() as session:
        result = await session.execute(
            select(Account).where(Account.id == account_number)
        )
        account = result.scalar_one_or_none()

    if account is None:
        await message.answer(f"Не нашёл аккаунт №{account_number}. Проверь номер.")
        return

    await bind_current_account(message.chat.id, account.id)
    await state.update_data(current_account_id=account.id)

    await message.answer(
        f"Переключился на «{account.display_name}» "
        f"({account.account_type.value}, ниша: {account.niche or '—'}).\n"
        f"Все дальнейшие команды — в его контексте."
    )
