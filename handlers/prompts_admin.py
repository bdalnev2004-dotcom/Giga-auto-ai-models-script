"""
Editing scenario briefs from the chat.

Briefs are the main lever on output quality, and until now pulling that lever
meant editing Python and redeploying. The operator works from Telegram, so the
lever needs a handle here (doc §6: versioned templates, editable without deploy).

    /brief                     — which briefs exist and which are edited
    /brief bio                 — show the brief actually in use
    /brief bio avoid <текст>   — change one field (writes a new version)
    /brief bio history         — version log
    /brief bio rollback 2      — restore an earlier version
    /brief bio reset           — back to the brief shipped in the code

Edits are farm-wide by default. Adding `local` scopes the change to the currently
selected account: `/brief bio local avoid ...`
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message

from handlers.account import get_current_account_id
from services import prompt_store
from services.prompts import BRIEFS

router = Router(name="prompts_admin")

USAGE = (
    "<b>Редактор брифов</b>\n"
    "<code>/brief</code> — список сценариев\n"
    "<code>/brief bio</code> — показать бриф\n"
    "<code>/brief bio avoid Не начинать с вопроса</code> — изменить поле\n"
    "<code>/brief bio history</code> — версии\n"
    "<code>/brief bio rollback 2</code> — откатить\n"
    "<code>/brief bio reset</code> — вернуть заводской\n\n"
    f"Поля: {', '.join(prompt_store.EDITABLE_FIELDS)}\n"
    "Добавь <code>local</code> после сценария, чтобы правка касалась только "
    "текущего аккаунта."
)


@router.message(F.text.regexp(r"(?i)^/brief\b"))
async def brief_command(message: Message):
    parts = message.text.split(maxsplit=1)
    args = parts[1].split() if len(parts) > 1 else []

    if not args:
        await message.answer(await _list_briefs(message.chat.id))
        return

    scenario_id = args[0].lower()
    if scenario_id not in BRIEFS:
        await message.answer(
            f"Сценария «{scenario_id}» нет. Есть: {', '.join(sorted(BRIEFS))}."
        )
        return

    rest = args[1:]
    account_id = None
    if rest and rest[0].lower() == "local":
        account_id = await get_current_account_id(message.chat.id)
        if account_id is None:
            await message.answer("Для <code>local</code> сначала выбери аккаунт: /account N")
            return
        rest = rest[1:]

    # /brief bio
    if not rest:
        brief = await prompt_store.resolve(scenario_id, account_id)
        overridden = await prompt_store.is_overridden(scenario_id, account_id)
        await message.answer(prompt_store.render(brief, scenario_id, overridden))
        return

    command = rest[0].lower()

    if command == "history":
        await message.answer(await _render_history(scenario_id, account_id))
        return

    if command == "reset":
        await prompt_store.reset(scenario_id, account_id)
        await message.answer(f"Бриф «{scenario_id}» возвращён к заводскому.")
        return

    if command == "rollback":
        if len(rest) < 2 or not rest[1].isdigit():
            await message.answer("Укажи версию: <code>/brief bio rollback 2</code>")
            return
        try:
            brief = await prompt_store.rollback(scenario_id, int(rest[1]), account_id)
        except prompt_store.PromptStoreError as e:
            await message.answer(str(e))
            return
        await message.answer(
            f"Откатил на версию {rest[1]}.\n\n"
            + prompt_store.render(brief, scenario_id, True)
        )
        return

    # /brief bio <field> <value>
    if command not in prompt_store.EDITABLE_FIELDS:
        await message.answer(
            f"Поле «{command}» не редактируется.\n"
            f"Доступны: {', '.join(prompt_store.EDITABLE_FIELDS)}."
        )
        return

    value = " ".join(rest[1:]).strip()
    if not value:
        label = prompt_store.FIELD_LABELS.get(command, command)
        current = getattr(await prompt_store.resolve(scenario_id, account_id), command)
        await message.answer(
            f"<b>{label}</b> сейчас:\n{current}\n\n"
            f"Чтобы изменить: <code>/brief {scenario_id} {command} новый текст</code>"
        )
        return

    try:
        brief = await prompt_store.save(
            scenario_id, command, value, account_id, edited_by=message.from_user.id
        )
    except prompt_store.PromptStoreError as e:
        await message.answer(str(e))
        return

    scope = "для этого аккаунта" if account_id else "для всей фермы"
    await message.answer(
        f"Обновил «{prompt_store.FIELD_LABELS.get(command, command)}» {scope}. "
        "Подействует на следующей генерации.\n\n"
        + prompt_store.render(brief, scenario_id, True)
    )


async def _list_briefs(chat_id: int) -> str:
    account_id = await get_current_account_id(chat_id)
    lines = [USAGE, "", "<b>Сценарии</b>"]
    for scenario_id in sorted(BRIEFS):
        marks = []
        if await prompt_store.is_overridden(scenario_id, None):
            marks.append("изменён")
        if account_id and await prompt_store.is_overridden(scenario_id, account_id):
            marks.append("свой у аккаунта")
        suffix = f" — {', '.join(marks)}" if marks else ""
        lines.append(f"• <code>{scenario_id}</code>{suffix}")
    return "\n".join(lines)


async def _render_history(scenario_id: str, account_id: int | None) -> str:
    entries = await prompt_store.history(scenario_id, account_id)
    if not entries:
        return f"У «{scenario_id}» правок не было — работает заводской бриф."

    lines = [f"<b>Версии «{scenario_id}»</b>"]
    for entry in entries:
        active = "  ← активна" if entry["active"] else ""
        stamp = entry["created_at"].strftime("%d.%m %H:%M")
        lines.append(f"v{entry['version']} · {entry['field']} · {stamp}{active}")
    lines.append("\nОткатить: <code>/brief {0} rollback N</code>".format(scenario_id))
    return "\n".join(lines)
