"""
Generic scenario runner. One router handles ALL scenarios (top-level and step)
because the flow is identical (doc §1/§3): match trigger -> ask clarifying
questions one at a time, pulling known answers from the account card -> generate
-> hand off to approval queue.

QUESTION_BANK is the direct translation of doc §3 into code. Extend per new
scenario; keep each list short and scenario-specific (don't over-ask).
"""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import json

from triggers import resolve_trigger, TOP_LEVEL_SCENARIOS
from fsm.states import ScenarioDialog
from handlers.account import get_current_account_id, bind_current_account
from services import claude_service, drive_service
from db.session import get_session
from db.models import Account, AccountType, GenerationJob, ContentStatus

router = Router(name="scenarios")

QUESTION_BANK: dict[str, list[str]] = {
    "brand_name": [
        "Ниша и категория товаров?",
        "Язык названия (рус / англ / гибрид)?",
        "Есть занятые названия конкурентов, которых избегать?",
    ],
    "bio": [
        "УТП — за что вас выбирают?",
        "Доставка/гарантии/акции для bio?",
        "Нужен призыв к действию на 3 строке?",
        "С эмодзи или строго текст?",
    ],
    "logo": [
        "Тип: текстовый леттеринг / иконка+текст / только знак?",
        "Палитра / запрещённые цвета?",
        "Настроение (минимализм, ретро, люкс)?",
    ],
    "reels_scripts": [
        "Товар/тема этого видео?",
        "Цель: охват / переход в TG или на сайт / продажа?",
        "Формат: обзор, распаковка, «до/после», топ-подборка?",
        "Музыка (бренд) или озвучка (блогерша)?",
    ],
    "voiceover_text": [
        "По какому сценарию (номер)?",
        "Длина ролика в секундах?",
        "Тон реплики: дружелюбно / экспертно / дерзко?",
    ],
    "carousel": [
        "Тема/угол из плейбука?",
        "Товар или чистый обучающий контент?",
        "Есть фото блогерши под слайды или пока типографика?",
    ],
    "reels_edit": [
        "Какой исходный ролик (номер)?",
        "Музыка или наложить войсовер? Или и то и другое?",
        "Субтитры: да/нет, язык, стиль?",
        "Использовать сохранённый шаблон монтажа этой блогерши?",
    ],
    "daily_story": [
        "Ссылку на какой Reels прикрепляем?",
    ],
    "create_brand": [
        "Стиль/тон/характер бренда (портрет)?",
    ],
    "create_blogger": [
        "Имя, возраст, тип внешности?",
        "Стиль/ниша (мода, лайфстайл, обзоры)?",
        "Какие товары обозревает?",
    ],
}


async def _get_account_context(account_id: int) -> dict:
    async with get_session() as session:
        account = await session.get(Account, account_id)
    if account is None:
        return {}
    return {
        "display_name": account.display_name,
        "niche": account.niche,
        "persona_summary": account.persona_summary,
        "voice_id": account.voice_id,
    }


@router.message(F.text)
async def handle_free_text(message: Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state == ScenarioDialog.collecting_answers.state:
        await _collect_answer(message, state)
        return

    kind, scenario_id = resolve_trigger(message.text)
    if kind not in ("top_level", "step"):
        return  # not a recognized trigger — let other handlers/nothing handle it

    account_id = get_current_account_id(message.chat.id)
    if account_id is None and kind == "step":
        await message.answer("Сначала выбери аккаунт: /account N")
        return

    await state.update_data(
        scenario_id=scenario_id,
        account_id=account_id,
        answers={},
    )
    await state.set_state(ScenarioDialog.collecting_answers)
    await _ask_next_or_generate(message, state)


async def _collect_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    scenario_id = data["scenario_id"]
    answers = data.get("answers", {})

    question_bank = QUESTION_BANK.get(scenario_id, [])
    answered_count = len(answers)
    if answered_count < len(question_bank):
        question_just_answered = question_bank[answered_count]
        answers[question_just_answered] = message.text

    await state.update_data(answers=answers)
    await _ask_next_or_generate(message, state)


async def _ask_next_or_generate(message: Message, state: FSMContext):
    data = await state.get_data()
    scenario_id = data["scenario_id"]
    answers = data.get("answers", {})
    question_bank = QUESTION_BANK.get(scenario_id, [])

    next_question = await claude_service.ask_next_question(scenario_id, question_bank, answers)
    if next_question:
        await message.answer(next_question)
        return

    # All questions answered.
    if scenario_id in TOP_LEVEL_SCENARIOS:
        await _create_account(message, state, scenario_id, answers)
        return

    await _generate_and_queue(message, state, scenario_id, answers, revision_notes=None)


async def _create_account(message: Message, state: FSMContext, scenario_id: str, answers: dict):
    """
    Closes the Level-2 gap: create_brand / create_blogger used to only collect
    answers and stop. Now it actually rows the Account, seeds AccountPlatform
    (all is_active=True but is_connected=False — real IDs come later per your
    call), builds the Drive folder tree, and binds this chat for reminders.
    """
    account_type = AccountType.brand if scenario_id == "create_brand" else AccountType.blogger
    display_name = list(answers.values())[0] if answers else "Без названия"
    persona_summary = "; ".join(f"{q}: {a}" for q, a in answers.items())

    async with get_session() as session:
        account = Account(
            display_name=display_name,
            account_type=account_type,
            persona_summary=persona_summary,
            status="setup",
        )
        session.add(account)
        await session.flush()  # get account.id before commit

        from db.models import AccountPlatform, Platform
        # Posting scope is Instagram Reels only (per clarification) — only
        # Instagram gets an active row. TikTok/YouTube/VK stay in the Platform
        # enum for future flexibility but aren't seeded as active here.
        session.add(
            AccountPlatform(account_id=account.id, platform=Platform.instagram, is_active=True)
        )

        await session.commit()
        account_id = account.id

    try:
        drive_folder_id = drive_service.create_account_folder_tree(display_name)
        async with get_session() as session:
            db_account = await session.get(Account, account_id)
            db_account.drive_folder_id = drive_folder_id
            db_account.status = "active"
            await session.commit()
    except Exception:
        # Drive/service-account may not be configured yet during architecture
        # phase — the account row still exists, just flagged for later setup.
        pass

    await bind_current_account(message.chat.id, account_id)

    await message.answer(
        f"Аккаунт «{display_name}» создан (№{account_id}). "
        f"Структура папок в Drive подготовлена. Контекст переключён на него — "
        f"можно сразу продолжать («лого», «bio», «сценарии»...)."
    )
    await state.clear()


async def _generate_and_queue(
    message: Message, state: FSMContext, scenario_id: str, answers: dict, revision_notes: str | None
):
    account_context = await _get_account_context((await state.get_data()).get("account_id"))
    result_text = await claude_service.generate_copy(scenario_id, account_context, answers, revision_notes)

    data = await state.get_data()
    async with get_session() as session:
        job = GenerationJob(
            account_id=data.get("account_id"),
            scenario_id=scenario_id,
            answers_json=json.dumps(answers, ensure_ascii=False),
            revision_notes=revision_notes,
            attempt=(data.get("attempt", 0) + 1),
            status=ContentStatus.pending_approval,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    # TODO: route by STEP_SCENARIOS[scenario_id]["service"] to Higgsfield /
    # ElevenLabs / Vyra / HikerAPI as appropriate; here we handle the text-only
    # path and hand off anything visual/batched to the same approval queue.
    await message.answer(f"Готово:\n\n{result_text}\n\nПодходит? (номер варианта / ✅ / ❌)")
    await state.update_data(job_id=job_id, answers=answers, attempt=job.attempt)
    await state.set_state(ScenarioDialog.awaiting_approval)
