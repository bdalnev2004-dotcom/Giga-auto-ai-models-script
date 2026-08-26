"""
Generic scenario runner. One router handles ALL scenarios (top-level and step)
because the flow is identical (doc §1/§3): match trigger -> ask clarifying
questions one at a time -> generate -> hand off to the approval queue.

Two interview modes:
  - top-level (create_brand / create_blogger) walks services.persona.interview_for(),
    keyed by PersonaCard field, and ends by writing the account's character card.
  - step scenarios walk QUESTION_BANK, keyed by question text.

QUESTION_BANK stays short on purpose: everything durable about the account lives
in the persona card and must not be re-asked per task.
"""
import json

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from triggers import resolve_trigger, TOP_LEVEL_SCENARIOS
from fsm.states import ScenarioDialog
from handlers.account import get_current_account_id, bind_current_account
from services import claude_service, drive_service
from services.persona import PersonaCard, interview_for
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
    ],
    "carousel": [
        "Тема/угол из плейбука?",
        "Товар или чистый обучающий контент?",
    ],
    "reels_edit": [
        "Какой исходный ролик (номер)?",
        "Музыка или наложить войсовер? Или и то и другое?",
        "Субтитры: да/нет, язык, стиль?",
    ],
    "daily_story": [
        "Ссылку на какой Reels прикрепляем?",
    ],
    "highlight_covers": [
        "Какие рубрики нужны (через запятую)?",
    ],
    "story_covers": [
        "Какие рубрики нужны (через запятую)?",
    ],
    "tg_post": [
        "О чём пост?",
    ],
}


async def _load_persona(account_id: int | None) -> PersonaCard:
    """The account's character card — the voice every generation is written in."""
    if account_id is None:
        return PersonaCard()
    async with get_session() as session:
        account = await session.get(Account, account_id)
    if account is None:
        return PersonaCard()
    card = PersonaCard.from_json(account.persona_json)
    if not card.display_name:
        card.display_name = account.display_name
    if not card.niche and account.niche:
        card.niche = account.niche
    return card


@router.message(F.text)
async def handle_free_text(message: Message, state: FSMContext):
    if await state.get_state() == ScenarioDialog.collecting_answers.state:
        await _collect_answer(message, state)
        return

    kind, scenario_id = resolve_trigger(message.text)
    if kind not in ("top_level", "step"):
        return  # not a recognized trigger

    account_id = get_current_account_id(message.chat.id)
    if account_id is None and kind == "step":
        await message.answer("Сначала выбери аккаунт: /account N")
        return

    await state.update_data(scenario_id=scenario_id, account_id=account_id, answers={})
    await state.set_state(ScenarioDialog.collecting_answers)
    await _ask_next_or_generate(message, state)


def _interview_steps(scenario_id: str) -> list[tuple[str, str]] | None:
    """Field-keyed interview for top-level scenarios; None for step scenarios."""
    if scenario_id not in TOP_LEVEL_SCENARIOS:
        return None
    return interview_for("brand" if scenario_id == "create_brand" else "blogger")


async def _collect_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    scenario_id = data["scenario_id"]
    answers = data.get("answers", {})

    steps = _interview_steps(scenario_id)
    if steps is not None:
        # Persona interview: key by card field so the answers assemble into a card.
        answered = len(answers)
        if answered < len(steps):
            answers[steps[answered][0]] = message.text
    else:
        bank = QUESTION_BANK.get(scenario_id, [])
        answered = len(answers)
        if answered < len(bank):
            answers[bank[answered]] = message.text

    await state.update_data(answers=answers)
    await _ask_next_or_generate(message, state)


async def _ask_next_or_generate(message: Message, state: FSMContext):
    data = await state.get_data()
    scenario_id = data["scenario_id"]
    answers = data.get("answers", {})

    steps = _interview_steps(scenario_id)
    if steps is not None:
        if len(answers) < len(steps):
            field, question = steps[len(answers)]
            await message.answer(f"<b>{len(answers) + 1}/{len(steps)}</b>  {question}")
            return
        await _create_account(message, state, scenario_id, answers)
        return

    bank = QUESTION_BANK.get(scenario_id, [])
    question = claude_service.ask_next_question(scenario_id, bank, answers)
    if question:
        await message.answer(question)
        return

    await _generate_and_queue(message, state, scenario_id, answers, revision_notes=None)


async def _create_account(message: Message, state: FSMContext, scenario_id: str, answers: dict):
    """
    Writes the Account plus its character card, seeds the Instagram platform row,
    and builds the Drive folder tree.
    """
    is_brand = scenario_id == "create_brand"
    account_type = AccountType.brand if is_brand else AccountType.blogger

    await message.answer("Собираю карточку персонажа…")
    card = await claude_service.build_persona_card(answers)

    # display_name comes from the card's own field. The previous version took
    # answers.values()[0], which for create_brand was the tone answer — accounts
    # ended up named "минимализм, дерзко".
    display_name = card.display_name.strip() if card.display_name else ""
    if not display_name or display_name.lower() in {"нет", "-", "—"}:
        display_name = f"{'Бренд' if is_brand else 'Блогер'} без названия"
        card.display_name = display_name

    async with get_session() as session:
        account = Account(
            display_name=display_name,
            account_type=account_type,
            niche=card.niche or None,
            persona_json=card.to_json(),
            status="setup",
        )
        session.add(account)
        await session.flush()

        from db.models import AccountPlatform, Platform
        # Posting scope is Instagram Reels only — no other platform row is seeded.
        session.add(
            AccountPlatform(account_id=account.id, platform=Platform.instagram, is_active=True)
        )
        await session.commit()
        account_id = account.id

    drive_note = ""
    try:
        drive_folder_id = drive_service.create_account_folder_tree(display_name)
        async with get_session() as session:
            db_account = await session.get(Account, account_id)
            db_account.drive_folder_id = drive_folder_id
            db_account.status = "active"
            await session.commit()
        drive_note = "Папки в Drive созданы."
    except Exception as e:
        # Report it. The old version swallowed this and still told the operator the
        # folders were ready, so a misconfigured Drive stayed invisible for days.
        drive_note = (
            f"⚠️ Папки в Drive НЕ созданы: {type(e).__name__}. "
            "Аккаунт сохранён, структуру нужно создать после настройки Drive."
        )

    await bind_current_account(message.chat.id, account_id)

    missing = card.missing_fields()
    gaps = f"\n\n⚠️ Не заполнено: {', '.join(missing)}." if missing else ""

    await message.answer(
        f"Аккаунт «{display_name}» создан (№{account_id}).\n{drive_note}\n"
        f"Контекст переключён на него — можно продолжать («лого», «bio», «сценарии»…).{gaps}"
    )
    await state.clear()


async def _generate_and_queue(
    message: Message,
    state: FSMContext,
    scenario_id: str,
    answers: dict,
    revision_notes: str | None,
    previous_attempt: str | None = None,
):
    data = await state.get_data()
    account_id = data.get("account_id")
    persona = await _load_persona(account_id)

    await message.answer("Генерирую варианты…")
    try:
        variants = await claude_service.generate_variants(
            scenario_id, persona, answers, revision_notes, previous_attempt
        )
    except claude_service.GenerationError as e:
        await message.answer(f"Не получилось сгенерировать: {e}\nПопробуй ещё раз или уточни задачу.")
        await state.clear()
        return

    async with get_session() as session:
        job = GenerationJob(
            account_id=account_id,
            scenario_id=scenario_id,
            answers_json=json.dumps(answers, ensure_ascii=False),
            revision_notes=revision_notes,
            attempt=(data.get("attempt", 0) + 1),
            status=ContentStatus.pending_approval,
        )
        session.add(job)
        await session.commit()
        job_id, attempt = job.id, job.attempt

    await message.answer(claude_service.render_variants(variants))
    await state.update_data(
        job_id=job_id,
        answers=answers,
        attempt=attempt,
        # Kept so an approved number resolves to its actual text, and so a ❌ can
        # show the model what it is being asked to improve on.
        variants=[{"number": v.number, "angle": v.angle, "text": v.text} for v in variants],
    )
    await state.set_state(ScenarioDialog.awaiting_approval)
