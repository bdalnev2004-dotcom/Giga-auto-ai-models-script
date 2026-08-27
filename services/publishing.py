"""
Publishing: turns an approved file into a live Instagram post.

The gap this closes: approvals.py decided *where* a result should go and then
said so in chat without calling anything. Nothing ever read credentials back out
of the vault, and no code path constructed an InstagramClient.

Credential handling rule (see services/vault.py): decrypt at the moment of use,
inside this module, and never return a plaintext secret to a handler.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from db.models import Account, AccountPlatform, ContentItem, ContentStatus, Credential, Platform
from db.session import get_session
from services import vault
from services.instagram_service import InstagramClient

logger = logging.getLogger(__name__)

SESSION_DIR = Path("secrets/sessions")

# Instagram is far more tolerant of a slow farm than a fast one. These are
# deliberately conservative: a handful of posts a day per account, never in
# lockstep across accounts.
MIN_SECONDS_BETWEEN_POSTS = 60 * 45


class PublishError(RuntimeError):
    pass


class NotConnected(PublishError):
    """Account has no usable credentials yet — a setup problem, not a failure."""


async def _credentials(account_id: int) -> dict[str, str]:
    """Decrypted credential bundle for this account. Never leaves this module."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Credential).where(
                    Credential.account_id == account_id,
                    Credential.platform == Platform.instagram,
                )
            )
        ).scalars().all()

    out: dict[str, str] = {}
    for row in rows:
        try:
            out[row.key_name] = vault.decrypt(row.value_encrypted)
        except Exception as e:
            raise PublishError(
                f"Не расшифровался «{row.key_name}» — скорее всего сменился "
                f"VAULT_ENCRYPTION_KEY. ({type(e).__name__})"
            ) from e
    return out


async def store_credential(account_id: int, key_name: str, value: str) -> None:
    """Encrypt-and-upsert. The plaintext is never written to a column or a log."""
    encrypted = vault.encrypt(value)
    async with get_session() as session:
        existing = (
            await session.execute(
                select(Credential).where(
                    Credential.account_id == account_id,
                    Credential.platform == Platform.instagram,
                    Credential.key_name == key_name,
                )
            )
        ).scalar_one_or_none()

        if existing:
            existing.value_encrypted = encrypted
        else:
            session.add(
                Credential(
                    account_id=account_id,
                    platform=Platform.instagram,
                    key_name=key_name,
                    value_encrypted=encrypted,
                )
            )
        await session.commit()


async def connection_status(account_id: int) -> dict:
    """What is still missing before this account can post."""
    creds = await _credentials(account_id)
    required = {"username", "password", "proxy"}
    missing = sorted(required - creds.keys())
    session_file = SESSION_DIR / f"{account_id}.json"
    return {
        "has_credentials": not missing,
        "missing": missing,
        "has_totp": "totp_secret" in creds,
        "has_session": session_file.exists(),
        "username": creds.get("username"),
    }


async def _client(account_id: int) -> InstagramClient:
    """
    Builds a logged-in client. The session file and proxy are pinned per account:
    reusing either across accounts is the single fastest way to get a farm banned.
    """
    creds = await _credentials(account_id)
    for key in ("username", "password"):
        if key not in creds:
            raise NotConnected(
                f"У аккаунта нет «{key}». Заполни креды: /connect {account_id}"
            )

    proxy = creds.get("proxy", "")
    if not proxy:
        raise NotConnected(
            "Для аккаунта не задан прокси. Публиковать десятки аккаунтов с одного "
            "IP — прямой путь к массовому бану, поэтому публикация остановлена."
        )

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    client = InstagramClient(str(SESSION_DIR / f"{account_id}.json"), proxy=proxy)
    await client.login(creds["username"], creds["password"], creds.get("totp_secret"))
    return client


async def _rate_limit_guard(account_id: int) -> None:
    """Refuses a post that follows too closely on the account's previous one."""
    from datetime import datetime, timedelta

    async with get_session() as session:
        last = await session.scalar(
            select(ContentItem.created_at)
            .where(
                ContentItem.account_id == account_id,
                ContentItem.status == ContentStatus.published,
            )
            .order_by(ContentItem.created_at.desc())
            .limit(1)
        )
    if last is None:
        return
    gap = datetime.utcnow() - last
    if gap < timedelta(seconds=MIN_SECONDS_BETWEEN_POSTS):
        wait = timedelta(seconds=MIN_SECONDS_BETWEEN_POSTS) - gap
        raise PublishError(
            f"Слишком рано: с прошлой публикации прошло {int(gap.total_seconds() // 60)} мин. "
            f"Подожди ещё {int(wait.total_seconds() // 60)} мин — это защита от бана."
        )


async def publish_reel(
    account_id: int,
    video_path: str | Path,
    caption: str,
    content_item_id: int | None = None,
) -> dict:
    """Uploads a finished reel and marks the ContentItem published."""
    await _rate_limit_guard(account_id)
    client = await _client(account_id)

    try:
        result = await client.post_reel(str(video_path), caption)
    except Exception as e:
        logger.exception("reel upload failed for account %s", account_id)
        raise PublishError(f"Instagram не принял рилс: {type(e).__name__}: {e}") from e

    await _mark_published(account_id, content_item_id, result.get("pk") or result.get("id"))
    return result


async def publish_story_with_link(
    account_id: int,
    media_path: str | Path,
    link_url: str,
    content_item_id: int | None = None,
) -> dict:
    """
    Story carrying a tappable link — the reason this project uses the private API
    at all, since the official Graph API cannot attach one.
    """
    client = await _client(account_id)
    try:
        result = await client.post_story_with_link(str(media_path), link_url)
    except Exception as e:
        logger.exception("story upload failed for account %s", account_id)
        raise PublishError(f"Instagram не принял сторис: {type(e).__name__}: {e}") from e

    await _mark_published(account_id, content_item_id, result.get("pk") or result.get("id"))
    return result


async def _mark_published(
    account_id: int, content_item_id: int | None, external_id: str | None
) -> None:
    async with get_session() as session:
        if content_item_id:
            item = await session.get(ContentItem, content_item_id)
            if item:
                item.status = ContentStatus.published

        platform_row = (
            await session.execute(
                select(AccountPlatform).where(
                    AccountPlatform.account_id == account_id,
                    AccountPlatform.platform == Platform.instagram,
                )
            )
        ).scalar_one_or_none()
        if platform_row and not platform_row.is_connected:
            # First successful post is the only honest proof the account works.
            platform_row.is_connected = True
        await session.commit()

    if external_id:
        logger.info("account %s published media %s", account_id, external_id)


async def verify_login(account_id: int) -> str:
    """
    One-off check that credentials work, run at setup rather than at 20:00 when a
    scheduled story is due. Returns the username it logged in as.
    """
    creds = await _credentials(account_id)
    client = await _client(account_id)
    await asyncio.sleep(0)  # login already happened inside _client
    return creds.get("username", "?")
