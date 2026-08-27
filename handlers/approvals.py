"""
'Папка -> номер -> ответ номером/эмодзи' is the one approval mechanic used for
everything (doc §8): logo, name, bio, highlight covers, story covers, reels,
scripts, photos, carousels, voiceovers, tg posts. This router is the single
implementation of that mechanic; scenario handlers just enter this state.
"""
import logging
import re

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from fsm.states import ScenarioDialog
from db.session import get_session
from db.models import GenerationJob, ContentStatus, AuditLogEntry, Role
from services import feedback

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
        await message.answer("На какую дату запланировать? (ГГГГ-ММ-ДД)")
        # TODO: capture the date in a dedicated state, write ContentItem.scheduled_for
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
    variants = data.get("variants", [])

    chosen = None
    if chosen_number is not None:
        chosen = _variant_by_number(data, chosen_number)
        if chosen is None:
            available = ", ".join(str(v["number"]) for v in variants) or "—"
            await message.answer(f"Нет варианта №{chosen_number}. Есть: {available}.")
            return
    elif len(variants) == 1:
        chosen = variants[0]
    elif variants:
        await message.answer("Их несколько — ответь номером, какой берём.")
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
    publish_via = PUBLISH_ROUTING.get(scenario_id)
    if publish_via:
        # TODO: call the adapter (instagram_service.post_*) once credentials and a
        # session file exist for this account. Routing is decided here; execution
        # is still the open piece.
        await message.answer(f"Принято ✅. Уйдёт на публикацию через: {publish_via}.{picked}")
    else:
        await message.answer(f"Принято ✅. Результат сохранён.{picked}")

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
