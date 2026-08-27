"""
Content library — the missing link between "a file was produced" and "the operator
can approve it by number".

Every generated unit is supposed to carry a sequence number that appears in three
places at once: the Drive filename (`reel_7.mp4`), the number typed in Telegram to
approve it, and ContentItem.sequence_number. Nothing wrote those rows before, so
the numbering mechanic the whole approval flow rests on had no backing store.

Drive calls go through asyncio.to_thread: googleapiclient is synchronous, and the
folder-tree call alone fires ~18 sequential HTTP requests — enough to freeze the
whole bot if run on the event loop.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select, func

from db.models import Account, ContentItem, ContentStatus
from db.session import get_session
from services import drive_service

# content_type -> the numbered Drive folder it belongs in (see DRIVE_FOLDER_TEMPLATE).
FOLDER_FOR_TYPE = {
    "persona": "01_персонаж",
    "avatar": "02_аватар",
    "photo": "03_фото",
    "carousel_template": "04_карусели/шаблон",
    "carousel_script": "04_карусели/сценарии",
    "reel_raw": "05_reels_сырые",
    "reel_edited": "06_reels_монтаж",
    "voiceover": "07_озвучка",
    "reel_script": "08_reels_сценарии",
    "highlight_cover": "09_highlights",
    "story": "10_сторис",
    "text": "11_тексты",
    "tg_post": "12_telegram",
    "reference": "13_референсы",
    "published": "14_опубликовано",
    "edit_template": "15_шаблон_монтажа",
    "uniquified": "16_уникализировано",
}

MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


async def next_sequence_number(account_id: int, content_type: str) -> int:
    """Numbering is per (account, content_type) — reel_1 and photo_1 coexist."""
    async with get_session() as session:
        current = await session.scalar(
            select(func.max(ContentItem.sequence_number)).where(
                ContentItem.account_id == account_id,
                ContentItem.content_type == content_type,
            )
        )
    return (current or 0) + 1


async def _resolve_folder(account: Account, content_type: str) -> str | None:
    """
    Drive id of the subfolder for this content type, or None when Drive is not
    configured — callers keep the local file and the DB row either way.
    """
    if not account.drive_folder_id:
        return None
    path = FOLDER_FOR_TYPE.get(content_type)
    if not path:
        return account.drive_folder_id

    folder_id = account.drive_folder_id
    for part in path.split("/"):
        children = await asyncio.to_thread(drive_service.list_numbered_files, folder_id)
        match = next((c for c in children if c["name"] == part), None)
        if match is None:
            return account.drive_folder_id  # tree incomplete — fall back to the root
        folder_id = match["id"]
    return folder_id


async def store(
    account_id: int,
    content_type: str,
    local_path: str | Path,
    linked_script_id: int | None = None,
    status: ContentStatus = ContentStatus.pending_approval,
) -> ContentItem:
    """
    Files the artefact: assigns the next number, uploads it to the right Drive
    folder, and writes the ContentItem the approval flow reads back.

    A Drive failure is recorded, not raised — losing the row as well would leave
    the file orphaned with nothing pointing at it.
    """
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"Нечего сохранять, файла нет: {local_path}")

    number = await next_sequence_number(account_id, content_type)
    filename = f"{content_type}_{number}{local_path.suffix}"

    async with get_session() as session:
        account = await session.get(Account, account_id)

    drive_file_id: str | None = None
    if account is not None:
        try:
            folder_id = await _resolve_folder(account, content_type)
            if folder_id:
                drive_file_id = await asyncio.to_thread(
                    drive_service.upload_numbered_file,
                    folder_id,
                    filename,
                    str(local_path),
                    MIME_BY_SUFFIX.get(local_path.suffix.lower(), "application/octet-stream"),
                )
        except Exception:
            # Deliberately swallowed: see docstring. The caller reports Drive state
            # from drive_file_id being None rather than from an exception.
            drive_file_id = None

    async with get_session() as session:
        item = ContentItem(
            account_id=account_id,
            content_type=content_type,
            sequence_number=number,
            drive_file_id=drive_file_id,
            status=status,
            linked_script_id=linked_script_id,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item
