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
from db.models import Account, ChatBinding, ChatContext

router = Router(name="account")

# In-process cache — fast path for the common case, but not the source of truth.
# It used to be the ONLY copy: after a bot restart it comes up empty, so every
# operator had to re-type /account N even though ChatBinding already remembered
# their choice. Now a cache miss falls through to the DB instead of returning None.
_current_account_cache: dict[int, int] = {}


async def get_current_account_id(chat_id: int) -> int | None:
    cached = _current_account_cache.get(chat_id)
    if cached is not None:
        return cached

    async with get_session() as session:
        context = await session.get(ChatContext, chat_id)
    account_id = context.account_id if context else None

    if account_id is not None:
        _current_account_cache[chat_id] = account_id
    return account_id


async def bind_current_account(chat_id: int, account_id: int) -> None:
    """
    Switches this chat's active account AND persists both records that depend on
    it, which are two genuinely different relationships and must not be conflated:

    - ChatBinding.is_primary is per-ACCOUNT: "which of this account's bound chats
      gets its 12:00/15:00/20:00 reminders" (see scheduler.py). An agency group
      chat can legitimately be the is_primary destination for several accounts
      at once — switching /account in that chat must NOT touch this.
    - ChatContext is per-CHAT: "which account is this chat currently working
      with" — the thing get_current_account_id() answers. Exactly one row per
      chat_id, upserted here.
    """
    _current_account_cache[chat_id] = account_id

    async with get_session() as session:
        result = await session.execute(
            select(ChatBinding).where(
                ChatBinding.account_id == account_id,
                ChatBinding.telegram_chat_id == chat_id,
            )
        )
        if result.scalar_one_or_none() is None:
            session.add(
                ChatBinding(account_id=account_id, telegram_chat_id=chat_id, is_primary=True)
            )

        context = await session.get(ChatContext, chat_id)
        if context is None:
            session.add(ChatContext(telegram_chat_id=chat_id, account_id=account_id))
        else:
            context.account_id = account_id

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
