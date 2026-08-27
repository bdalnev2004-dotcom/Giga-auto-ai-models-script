"""
Video intake and the uniquification chain.

This closes the biggest hole in the pipeline: every handler was `F.text`, so a
video sent to the bot was silently dropped. There was no way to get a file into
the system at all, which made services/uniquify_service.py unreachable — the
`uniquify` trigger fell through to the copywriting path and asked Claude to write
marketing variants about the phrase "уникализировать".

Flow: operator sends a reel -> it is downloaded to a temp file and remembered per
chat -> "уникализировать" runs ffmpeg over it -> the result is filed into
16_уникализировано with a sequence number and sent back for approval.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from tempfile import gettempdir

from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext

from db.models import ContentStatus
from handlers.account import get_current_account_id
from services import library, uniquify_service

logger = logging.getLogger(__name__)
router = Router(name="media")

WORKDIR = Path(gettempdir()) / "giga-bot"

# Telegram's Bot API refuses downloads over 20 MB. A finished vertical reel is
# usually well under that, but a raw export is not — so the limit is reported
# explicitly instead of failing deep inside the download call.
MAX_DOWNLOAD_MB = 20


def _chat_dir(chat_id: int) -> Path:
    path = WORKDIR / str(chat_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.message(F.video | F.document | F.video_note | F.animation)
async def receive_video(message: Message, state: FSMContext):
    """Accepts a video and parks it as this chat's 'current file'."""
    media = message.video or message.document or message.video_note or message.animation
    file_name = getattr(media, "file_name", None) or f"{media.file_unique_id}.mp4"
    suffix = Path(file_name).suffix.lower() or ".mp4"

    if suffix not in {".mp4", ".mov", ".m4v"}:
        await message.answer(
            f"Формат {suffix or 'без расширения'} не поддерживается — нужен mp4 или mov."
        )
        return

    size_mb = (media.file_size or 0) / 1_048_576
    if size_mb > MAX_DOWNLOAD_MB:
        await message.answer(
            f"Файл {size_mb:.0f} МБ — Telegram отдаёт ботам максимум {MAX_DOWNLOAD_MB} МБ.\n"
            "Загрузи ролик в папку аккаунта на Drive и работай оттуда."
        )
        return

    dest = _chat_dir(message.chat.id) / f"src_{media.file_unique_id}{suffix}"
    notice = await message.answer("Скачиваю…")
    try:
        await message.bot.download(media, destination=dest)
    except Exception as e:
        logger.exception("download failed")
        await notice.edit_text(f"Не смог скачать: {type(e).__name__}: {e}")
        return

    await state.update_data(current_video=str(dest))
    await notice.edit_text(
        f"Принял ролик ({size_mb:.1f} МБ).\n"
        "Дальше: «уникализировать» — сделаю технически другую копию под перезалив."
    )


# Substring match, matching how resolve_trigger behaves everywhere else — the
# operator should be able to write "уникализируй этот ролик", not just the keyword.
UNIQUIFY_RE = r"(?i)(уникализ|сделать уникальн)"


@router.message(F.text.regexp(UNIQUIFY_RE))
async def run_uniquify(message: Message, state: FSMContext):
    """
    Runs the ffmpeg pass over the chat's current video.

    Registered here rather than going through the generic scenario runner: this is
    a file operation, not a copywriting task, and the generic path would hand it
    to Claude.
    """
    data = await state.get_data()
    src = data.get("current_video")
    if not src or not Path(src).exists():
        await message.answer("Сначала пришли ролик — я его уникализирую и верну.")
        return

    account_id = get_current_account_id(message.chat.id)
    if account_id is None:
        await message.answer(
            "Сначала выбери аккаунт: /account N — от него зависят параметры обработки."
        )
        return

    # variant = how many uniquified copies this account already has, so a second
    # pass over the same source yields a different treatment rather than a repeat.
    variant = await library.next_sequence_number(account_id, "uniquified") - 1
    dst = Path(src).with_name(f"uniq_{account_id}_{variant}.mp4")

    notice = await message.answer("Уникализирую… (перекодирование, это не мгновенно)")
    try:
        recipe = await uniquify_service.uniquify(
            src, dst, account_id=account_id, variant=variant
        )
    except uniquify_service.UniquifyError as e:
        await notice.edit_text(f"Не получилось: {e}")
        return

    await notice.edit_text(f"Готово. Применено: {recipe.describe()}\nСохраняю…")

    try:
        item = await library.store(
            account_id, "uniquified", dst, status=ContentStatus.pending_approval
        )
    except Exception as e:
        logger.exception("library.store failed")
        await notice.edit_text(
            f"Ролик обработан, но сохранить не смог: {type(e).__name__}: {e}\n"
            "Файл отправлю в чат — забери оттуда."
        )
        await message.answer_video(FSInputFile(dst))
        return

    where = (
        "Загружен в Drive → 16_уникализировано."
        if item.drive_file_id
        else "⚠️ В Drive не загрузился (проверь настройку) — держи файл здесь."
    )
    await notice.edit_text(f"Готово: uniquified_{item.sequence_number}.mp4\n{where}")
    await message.answer_video(
        FSInputFile(dst),
        caption=f"№{item.sequence_number} · {recipe.describe()}",
    )


@router.message(F.text.regexp(r"(?i)(забыть|сбросить) ролик"))
async def forget_video(message: Message, state: FSMContext):
    data = await state.get_data()
    src = data.get("current_video")
    if src:
        await asyncio.to_thread(lambda: Path(src).unlink(missing_ok=True))
    await state.update_data(current_video=None)
    await message.answer("Ок, забыл текущий ролик.")
