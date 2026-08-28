"""
'Папка -> номер -> ответ номером/эмодзи' is the one approval mechanic used for
everything (doc §8): logo, name, bio, highlight covers, story covers, reels,
scripts, photos, carousels, voiceovers, tg posts. This router is the single
implementation of that mechanic; scenario handlers just enter this state.
"""
import logging
import re
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from fsm.states import ScenarioDialog
from db.session import get_session
from db.models import GenerationJob, ContentStatus, AuditLogEntry, Role
from services import feedback, publishing

logger = logging.getLogger(__name__)
router = Router(name="approvals")

# Which backend service ultimately PUBLISHES each step's approved output.
# Automated posting is Instagram Reels ONLY. Photos/carousels/stories can still be
# generated and approved as content, but nothing except reels_edit auto-publishes.
# tg_post goes to the account's own Telegram channel — a content channel, not a
# social "posting platform" in this sense.
PUBLISH_ROUTING = {
    "reels_edit": "instagram",
    "tg_post": "telegram_channel",
}

EMOJI_APPROVE = {"✅", "👍", "❤️"}
EMOJI_REJECT = {"❌", "👎"}
EMOJI_SCHEDULE = {"📅"}

NUMBER_RE = re.compile(r"^\d+$")


def _variant_by_number(data: dict, number: int) -> dict | None:
    return next((v for v in data.get("variants", []) if v["number"] == number), None)


def _resolve_chosen(data: dict, chosen_number: int | None) -> tuple[dict | None, str | None]:
    """
    Shared by ✅ and 📅: a number picks that variant, no number infers the single
    remaining one, and "several with no number" is reported rather than guessed.
    Returns (chosen, error_message) — exactly one of the two is set.
    """
    variants = data.get("variants", [])
    if chosen_number is not None:
        chosen = _variant_by_number(data, chosen_number)
        if chosen is None:
            available = ", ".join(str(v["number"]) for v in variants) or "—"
            return None, f"Нет варианта №{chosen_number}. Есть: {available}."
        return chosen, None
    if len(variants) == 1:
        return variants[0], None
    if variants:
        return None, "Их несколько — ответь номером, какой берём."
    return None, None


def _all_variants_text(data: dict) -> str:
    return "\n\n".join(
        f"{v['number']}. {v['angle']}\n{v['text']}" for v in data.get("variants", [])
    )


@router.message(ScenarioDialog.awaiting_approval, F.text)
async def handle_approval_reply(message: Message, state: FSMContext):
    text = message.text.strip()

    if text in EMOJI_APPROVE:
        await _approve(message, state, chosen_number=None)
    elif text in EMOJI_REJECT:
        await message.answer("Что именно не понравилось / что исправить?")
        await state.set_state(ScenarioDialog.awaiting_revision_notes)
    elif text in EMOJI_SCHEDULE:
        await _start_schedule(message, state)
    elif NUMBER_RE.match(text):
        await _approve(message, state, chosen_number=int(text))
    elif re.search(r"\d", text):
        # A list of numbers means "these ones are wrong" — the doc §8 culling loop.
        await _reject_batch(message, state, [int(n) for n in re.findall(r"\d+", text)])
    else:
        await message.answer("Ответь номером варианта, списком номеров, или ✅ / ❌ / 📅.")


async def _approve(message: Message, state: FSMContext, chosen_number: int | None):
    data = await state.get_data()
    scenario_id = data.get("scenario_id")
    job_id = data.get("job_id")
    account_id = data.get("account_id")
    chosen, error = _resolve_chosen(data, chosen_number)
    if error:
        await message.answer(error)
        return

    if job_id:
        async with get_session() as session:
            job = await session.get(GenerationJob, job_id)
            if job:
                job.status = ContentStatus.approved
                session.add(
                    AuditLogEntry(
                        account_id=job.account_id,
                        telegram_user_id=message.from_user.id,
                        role=Role.operator,
                        action=f"approved:{chosen['number']}" if chosen else "approved",
                    )
                )
                await session.commit()

    # Filing the approved text is what lets later generations imitate it instead of
    # starting from the same static brief every time (services/feedback.py).
    if chosen and account_id:
        try:
            await feedback.remember_approved(account_id, scenario_id, chosen["text"])
        except Exception:
            logger.exception("could not file approved text")

    picked = f"\n\nВзяли вариант {chosen['number']} — {chosen['angle']}." if chosen else ""

    if PUBLISH_ROUTING.get(scenario_id) == "instagram" and account_id:
        caption = (chosen or {}).get("text", "")
        await _publish_to_instagram(message, data, account_id, picked, caption)
    else:
        await message.answer(f"Принято ✅. Результат сохранён.{picked}")

    await state.clear()


async def _publish_to_instagram(
    message: Message, data: dict, account_id: int, picked: str, caption: str
) -> None:
    """
    The approved reel actually goes out here. Everything that can stop a publish —
    no file, no credentials, no session, too soon since the last post — is reported
    as a specific next step rather than a generic failure.
    """
    video = data.get("current_video") or data.get("approved_video")
    if not video or not Path(video).exists():
        await message.answer(
            f"Принято ✅.{picked}\n\n"
            "Публиковать нечего — файла нет. Пришли готовый ролик в чат, "
            "тогда утверждение отправит его в Instagram."
        )
        return

    status = await publishing.connection_status(account_id)
    if not status["has_credentials"]:
        await message.answer(
            f"Принято ✅.{picked}\n\n"
            f"⚠️ Аккаунт не подключён — не хватает: {', '.join(status['missing'])}.\n"
            f"Заполни: <code>/connect {account_id}</code>"
        )
        return
    if not status["has_session"]:
        await message.answer(
            f"Принято ✅.{picked}\n\n"
            f"⚠️ Нет файла сессии. Разовый вход: <code>/login {account_id}</code>"
        )
        return

    notice = await message.answer("Публикую в Instagram…")
    try:
        await publishing.publish_reel(account_id, video, caption)
    except publishing.PublishError as e:
        await notice.edit_text(f"Принято ✅.{picked}\n\n⚠️ Не опубликовалось: {e}")
        return

    await notice.edit_text(f"Опубликовано в Instagram ✅{picked}")


async def _start_schedule(message: Message, state: FSMContext) -> None:
    """
    📅 used to ask for a date and then forget it — the state never actually
    changed, so the next message (typically the date, which contains digits)
    fell straight through to the batch-rejection branch below and produced
    something like "Таких вариантов нет: 2026, 08, 27". Fixed by actually
    entering a dedicated state and remembering which variant was picked.
    """
    data = await state.get_data()
    chosen, error = _resolve_chosen(data, chosen_number=None)
    if error:
        await message.answer(error)
        return

    await state.update_data(schedule_chosen=chosen)
    await state.set_state(ScenarioDialog.awaiting_schedule_date)
    await message.answer("На какую дату запланировать? Формат: ГГГГ-ММ-ДД")


@router.message(ScenarioDialog.awaiting_schedule_date, F.text)
async def handle_schedule_date(message: Message, state: FSMContext) -> None:
    from datetime import date, datetime

    text = message.text.strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        await message.answer(
            "Не разобрал дату. Формат строго ГГГГ-ММ-ДД, например 2026-09-15."
        )
        return  # stays in awaiting_schedule_date — no silent fallthrough

    if parsed.date() < date.today():
        await message.answer("Дата в прошлом. Укажи сегодняшнюю или будущую.")
        return

    data = await state.get_data()
    chosen = data.get("schedule_chosen")
    scenario_id = data.get("scenario_id")
    account_id = data.get("account_id")

    if chosen and account_id and scenario_id:
        try:
            await feedback.remember_scheduled(account_id, scenario_id, chosen["text"], parsed)
        except Exception:
            logger.exception("could not file scheduled text")
            await message.answer("Дату принял, но сохранить не смог — сообщи, если повторится.")
            await state.clear()
            return

    await message.answer(
        f"Запланировано на {parsed.date().isoformat()}. "
        "⚠️ Автопубликация по расписанию для этого пока не подключена — "
        "запись сохранена, но никто не заберёт её в этот день сам."
    )
    await state.clear()


async def _reject_batch(message: Message, state: FSMContext, rejected_numbers: list[int]):
    data = await state.get_data()
    variants = data.get("variants", [])
    known = {v["number"] for v in variants}
    unknown = [n for n in rejected_numbers if n not in known]
    if unknown:
        await message.answer(f"Таких вариантов нет: {', '.join(map(str, unknown))}.")
        return

    await state.update_data(rejected_numbers=rejected_numbers)
    await message.answer(
        f"Ок, варианты {', '.join(map(str, rejected_numbers))} — мимо. Что в них не так?"
    )
    await state.set_state(ScenarioDialog.awaiting_revision_notes)


@router.message(ScenarioDialog.awaiting_revision_notes, F.text)
async def handle_revision_notes(message: Message, state: FSMContext):
    data = await state.get_data()
    scenario_id = data.get("scenario_id")
    answers = data.get("answers", {})

    if scenario_id is None:
        await message.answer("Потерял контекст сценария — начни заново командой-триггером.")
        await state.clear()
        return

    # Hand the rejected work back to the model, not just the complaint: without it
    # the retry has no idea what it is supposed to move away from.
    rejected = data.get("rejected_numbers")
    if rejected:
        previous = "\n\n".join(
            f"{v['number']}. {v['text']}"
            for v in data.get("variants", [])
            if v["number"] in rejected
        )
    else:
        previous = _all_variants_text(data)

    from handlers.scenarios import _generate_and_queue

    await _generate_and_queue(
        message, state, scenario_id, answers,
        revision_notes=message.text,
        previous_attempt=previous or None,
    )
