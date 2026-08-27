"""
Entering Instagram credentials for an account.

publishing.py could read and decrypt credentials but nothing could write them, so
no account could ever be connected and publishing was unreachable.

Secrets never appear in a bot reply, and the message the operator typed them into
is deleted immediately — Telegram history is not a place to leave a password.
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from handlers.account import get_current_account_id
from services import publishing

logger = logging.getLogger(__name__)
router = Router(name="connect")

# Order matters: proxy is asked first because refusing to post without one is a
# hard rule, and finding out at the end is worse than being asked up front.
STEPS: list[tuple[str, str, bool]] = [
    ("proxy", "Прокси для этого аккаунта.\n"
              "Формат: <code>http://user:pass@host:port</code>\n"
              "Резидентный, свой на каждый аккаунт — общий IP на всю ферму даёт "
              "массовый бан.", True),
    ("username", "Логин Instagram (без @).", False),
    ("password", "Пароль.\nСообщение удалю сразу после сохранения.", True),
    ("totp_secret", "Секрет двухфакторки — строка из приложения-аутентификатора.\n"
                    "Если 2FA не включена, напиши <code>нет</code>.", True),
]


class Connect(StatesGroup):
    filling = State()


@router.message(F.text.regexp(r"(?i)^/connect\b"))
async def start_connect(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) > 1 and parts[1].isdigit():
        account_id = int(parts[1])
    else:
        account_id = get_current_account_id(message.chat.id)

    if account_id is None:
        await message.answer(
            "Укажи аккаунт: <code>/connect 3</code> — или выбери его через /account N."
        )
        return

    await state.set_state(Connect.filling)
    await state.update_data(connect_account_id=account_id, connect_step=0)
    await message.answer(
        f"Подключаю аккаунт №{account_id}. Четыре шага, отменить — <code>/cancel</code>."
    )
    await message.answer(f"<b>1/{len(STEPS)}</b>  {STEPS[0][1]}")


@router.message(Connect.filling, F.text.regexp(r"(?i)^/cancel\b"))
async def cancel_connect(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменил, ничего не сохранил.")


@router.message(Connect.filling, F.text)
async def collect_credential(message: Message, state: FSMContext):
    data = await state.get_data()
    account_id = data["connect_account_id"]
    index = data["connect_step"]
    key_name, _, is_secret = STEPS[index]

    value = message.text.strip()

    if is_secret:
        # Remove it from the chat before doing anything else — if saving fails,
        # the secret should still not be sitting in the history.
        try:
            await message.delete()
        except Exception:
            logger.warning("could not delete a message containing a secret")

    skipped = key_name == "totp_secret" and value.lower() in {"нет", "no", "-", "—"}
    if not skipped:
        try:
            await publishing.store_credential(account_id, key_name, value)
        except Exception as e:
            logger.exception("store_credential failed")
            await message.answer(f"Не сохранил «{key_name}»: {type(e).__name__}: {e}")
            await state.clear()
            return

    index += 1
    if index < len(STEPS):
        await state.update_data(connect_step=index)
        await message.answer(f"<b>{index + 1}/{len(STEPS)}</b>  {STEPS[index][1]}")
        return

    await state.clear()
    status = await publishing.connection_status(account_id)
    await message.answer(
        f"Готово. Аккаунт №{account_id} — <code>{status['username']}</code>.\n"
        f"Двухфакторка: {'есть' if status['has_totp'] else 'нет'}.\n\n"
        "Теперь нужен разовый вход, чтобы получить файл сессии: "
        f"<code>/login {account_id}</code>\n"
        "Instagram не любит частые входы, поэтому сессия сохраняется и переиспользуется."
    )


@router.message(F.text.regexp(r"(?i)^/login\b"))
async def do_login(message: Message):
    parts = message.text.split()
    account_id = (
        int(parts[1]) if len(parts) > 1 and parts[1].isdigit()
        else get_current_account_id(message.chat.id)
    )
    if account_id is None:
        await message.answer("Укажи аккаунт: <code>/login 3</code>")
        return

    status = await publishing.connection_status(account_id)
    if not status["has_credentials"]:
        await message.answer(
            f"Не хватает: {', '.join(status['missing'])}.\n"
            f"Заполни через <code>/connect {account_id}</code>."
        )
        return

    notice = await message.answer("Захожу в Instagram… это может занять минуту.")
    try:
        username = await publishing.verify_login(account_id)
    except publishing.NotConnected as e:
        await notice.edit_text(str(e))
        return
    except Exception as e:
        logger.exception("login failed for account %s", account_id)
        await notice.edit_text(
            f"Вход не удался: {type(e).__name__}: {e}\n\n"
            "Частые причины: запрос кода подтверждения, прокси не отвечает, "
            "или Instagram требует пройти проверку в приложении."
        )
        return

    await notice.edit_text(
        f"Вошёл как <code>{username}</code>. Файл сессии сохранён — "
        "дальше вход будет переиспользовать его, а не логиниться заново."
    )


@router.message(F.text.regexp(r"(?i)^/status\b"))
async def show_status(message: Message):
    parts = message.text.split()
    account_id = (
        int(parts[1]) if len(parts) > 1 and parts[1].isdigit()
        else get_current_account_id(message.chat.id)
    )
    if account_id is None:
        await message.answer("Укажи аккаунт: <code>/status 3</code>")
        return

    s = await publishing.connection_status(account_id)
    lines = [
        f"<b>Аккаунт №{account_id}</b>",
        f"Логин: {s['username'] or '—'}",
        f"Креды: {'заполнены' if s['has_credentials'] else 'не хватает ' + ', '.join(s['missing'])}",
        f"Двухфакторка: {'есть' if s['has_totp'] else 'нет'}",
        f"Сессия: {'есть' if s['has_session'] else 'нет — нужен /login'}",
    ]
    if s["has_credentials"] and s["has_session"]:
        lines.append("\nГотов к публикации.")
    return await message.answer("\n".join(lines))
