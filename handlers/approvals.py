"""
'Papka -> number -> reply with a number/emoji' is the one approval mechanic used
for everything (doc §8): logo, name, bio, highlight covers, story covers, reels,
scripts, photos, carousels, voiceovers, tg posts. This router is the single
implementation of that mechanic; scenario handlers just enter this state.
"""
import re

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from fsm.states import ScenarioDialog
from services import drive_service, claude_service
from triggers import STEP_SCENARIOS
from db.session import get_session
from db.models import GenerationJob, ContentStatus, AuditLogEntry, Role

router = Router(name="approvals")

# Which backend service ultimately PUBLISHES each step's approved output.
# This is the routing table that used to be a bare TODO — still calls into
# stub methods (hikerapi_service etc. raise NotImplementedError), but the
# decision of "who handles this content_type" is now real, not a comment.
# Publishing scope, per explicit call: automated posting is Instagram Reels
# ONLY (via HikerAPI). Photos/carousels/stories can still be generated and
# approved as content (feed grid, Drive library), but nothing except
# reels_edit auto-publishes. tg_post goes to the account's own Telegram
# channel, which is a content channel, not a "posting platform" in this sense
# — left in for AI-blogger accounts that route traffic there.
PUBLISH_ROUTING = {
    "reels_edit": "hikerapi",         # finished reel -> Instagram Reels — the only auto-publish path
    "tg_post": "telegram_channel",    # account's own TG channel (not a social "post")
    # daily_photo / daily_story / carousel: approved but NOT auto-published —
    # they stay in Drive as content, or wait for a manual step later if that
    # scope gets added back.
}

EMOJI_APPROVE = {"✅", "👍", "❤️"}
EMOJI_REJECT = {"❌", "👎"}
EMOJI_SCHEDULE = {"📅"}

NUMBER_RE = re.compile(r"^\d+$")


@router.message(ScenarioDialog.awaiting_approval, F.text)
async def handle_approval_reply(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()

    if text in EMOJI_APPROVE:
        await _approve(message, state, chosen_number=None)
    elif text in EMOJI_REJECT:
        await message.answer("Что именно не понравилось / что исправить?")
        await state.set_state(ScenarioDialog.awaiting_revision_notes)
    elif text in EMOJI_SCHEDULE:
        await message.answer("На какую дату запланировать? (ГГГГ-ММ-ДД)")
        # TODO: capture date in a dedicated state, write ContentItem.scheduled_for
    elif NUMBER_RE.match(text):
        await _approve(message, state, chosen_number=int(text))
    elif "," in text or " " in text:
        # batch rejection list, e.g. "2, 5, 7" — doc §8 script/photo culling loop
        rejected_numbers = [int(n) for n in re.findall(r"\d+", text)]
        await _reject_batch(message, state, rejected_numbers)
    else:
        await message.answer("Ответь номером варианта, списком номеров, или ✅ / ❌ / 📅.")


async def _approve(message: Message, state: FSMContext, chosen_number: int | None):
    data = await state.get_data()
    scenario_id = data.get("scenario_id")
    job_id = data.get("job_id")
    folder_id = data.get("current_drive_folder_id")

    if folder_id and chosen_number is not None:
        files = drive_service.list_numbered_files(folder_id)
        matched = next((f for f in files if str(chosen_number) in f["name"]), None)
        if matched is None:
            await message.answer(f"Не нашёл файл с номером {chosen_number} в папке.")
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
                        action="approved",
                    )
                )
                await session.commit()

    publish_via = PUBLISH_ROUTING.get(scenario_id)
    if publish_via:
        # TODO: actually call the adapter (hikerapi_service.post_*, etc.) once
        # credentials/session_file exist for this account+platform — routing
        # decision is made here, execution is still the Level-1 API work.
        await message.answer(f"Принято ✅. Уйдёт на публикацию через: {publish_via}.")
    else:
        await message.answer("Принято ✅. Результат сохранён, можно переходить к следующему шагу.")

    await state.clear()


async def _reject_batch(message: Message, state: FSMContext, rejected_numbers: list[int]):
    # TODO: for batched visual content (photos/carousels), flag the specific
    # ContentItem rows and regenerate only those numbers via the originating
    # media service, keeping count until doc §9 targets are hit. For single-
    # result text scenarios (bio, script, copy) the whole batch is one item —
    # route straight into the same revision-notes flow as a plain ❌.
    await message.answer(f"Ок, перегенерирую варианты №{rejected_numbers}. Что именно поправить?")
    await state.set_state(ScenarioDialog.awaiting_revision_notes)


@router.message(ScenarioDialog.awaiting_revision_notes, F.text)
async def handle_revision_notes(message: Message, state: FSMContext):
    notes = message.text
    data = await state.get_data()
    scenario_id = data.get("scenario_id")
    answers = data.get("answers", {})

    if scenario_id is None:
        await message.answer("Потерял контекст сценария — начни заново командой-триггером.")
        await state.clear()
        return

    # Re-run generation with the complaint folded in — closes the reject loop
    # that previously just echoed the notes back without acting on them.
    from handlers.scenarios import _generate_and_queue
    await _generate_and_queue(message, state, scenario_id, answers, revision_notes=notes)
