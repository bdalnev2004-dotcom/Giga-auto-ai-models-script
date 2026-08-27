"""
Core data model — matches the "account card = platform matrix" design from the doc (§4).

Secrets (passwords, session files, tokens) never live as plaintext columns here.
`Credential.value_encrypted` holds Fernet-encrypted bytes; decrypt only at the
moment of posting, on the backend, never returned to a bot/dashboard client.
"""
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    String, Integer, ForeignKey, DateTime, Boolean, Enum as SAEnum, LargeBinary, Text
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountType(str, Enum):
    brand = "brand"
    blogger = "blogger"


class Platform(str, Enum):
    instagram = "instagram"


class ContentStatus(str, Enum):
    draft = "draft"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    scheduled = "scheduled"
    published = "published"


class Role(str, Enum):
    admin = "admin"
    operator = "operator"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))  # Brand Name / blogger name
    account_type: Mapped[AccountType] = mapped_column(SAEnum(AccountType))
    niche: Mapped[str | None] = mapped_column(String(255), nullable=True)
    persona_summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # legacy free-text; superseded by persona_json
    persona_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # PersonaCard, see services/persona.py
    voice_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # ElevenLabs
    # Face consistency (see services/higgsfield_service.py): the approved frame the
    # persona is generated back toward, and an optionally trained Higgsfield
    # character id, which is stronger but must be created in their product first.
    anchor_image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    custom_reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    launch_wave: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="setup")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    platforms: Mapped[list["AccountPlatform"]] = relationship(back_populates="account")
    credentials: Mapped[list["Credential"]] = relationship(back_populates="account")
    content_items: Mapped[list["ContentItem"]] = relationship(back_populates="account")
    chat_bindings: Mapped[list["ChatBinding"]] = relationship(back_populates="account")
    generation_jobs: Mapped[list["GenerationJob"]] = relationship(back_populates="account")


class AccountPlatform(Base):
    """
    Which platforms are switched on for this account — the 'matrix' toggle row.

    Instagram is the only platform in scope, so today this table always holds one
    row per account. The shape is kept (rather than folding the fields into Account)
    because per-platform credentials and connection state are genuinely separate
    concerns, and collapsing them would be the harder thing to undo.

    external_account_id holds Instagram's own user id for this account. Left
    nullable — the account can exist and be planned before the real IG account
    is registered.
    is_connected flips true only once external_account_id + credentials are both set;
    the dispatcher in approvals.py checks this before attempting to publish.
    """
    __tablename__ = "account_platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    platform: Mapped[Platform] = mapped_column(SAEnum(Platform))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    external_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    account: Mapped["Account"] = relationship(back_populates="platforms")


class Credential(Base):
    """
    Encrypted per-account, per-platform secret blob: IG password, TOTP secret,
    proxy, session file. Farm-wide API keys (Anthropic, HikerAPI, ElevenLabs)
    live in config/env, NOT here — they are not per-account secrets.
    """
    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    platform: Mapped[Platform] = mapped_column(SAEnum(Platform))
    key_name: Mapped[str] = mapped_column(String(128))  # e.g. "password", "totp_secret", "refresh_token"
    value_encrypted: Mapped[bytes] = mapped_column(LargeBinary)

    account: Mapped["Account"] = relationship(back_populates="credentials")


class ContentItem(Base):
    """
    One row per generated unit (photo, reel, carousel, script, voiceover...).
    `sequence_number` matches the number used in the Drive filename AND the
    number the person types in Telegram to approve/reject it — this is the
    thread that ties the whole approval mechanic together (doc §8).
    """
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    content_type: Mapped[str] = mapped_column(String(64))  # "reel_script", "photo", "carousel", ...
    sequence_number: Mapped[int] = mapped_column(Integer)
    drive_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[ContentStatus] = mapped_column(SAEnum(ContentStatus), default=ContentStatus.draft)
    # The approved text itself, for text scenarios (bio, script, caption...).
    # Approved copy used to vanish once the dialog cleared, which threw away the
    # single most useful signal the farm produces: examples of what this account's
    # owner actually says yes to. services/feedback.py reads these back.
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_script_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id"), nullable=True
    )  # e.g. voiceover_5 <-> reels_script_5 <-> reels_5
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship(back_populates="content_items")


class ChatBinding(Base):
    """
    Maps a Telegram chat_id to an account so the scheduler (12:00/15:00/20:00 jobs)
    and any account-scoped notification knows where to send messages. One account
    can have several bound chats (e.g. an agency group + the account owner's DM);
    one chat can be bound to several accounts (the operator switches between them
    with /account N — see handlers/account.py).
    """
    __tablename__ = "chat_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    # BigInteger, not Integer: supergroup ids look like -1001234567890 and user ids
    # already exceed 2^31 — Telegram documents up to 52 significant bits.
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)  # where scheduled jobs post

    account: Mapped["Account"] = relationship(back_populates="chat_bindings")


class GenerationJob(Base):
    """
    Remembers *how* a batch of ContentItems was produced, so a ❌ can regenerate
    with the same scenario + original answers + accumulated revision notes,
    instead of the bot forgetting context after the first pass (closes the
    Level-2 gap: reject loop was previously just a text acknowledgement).
    """
    __tablename__ = "generation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    scenario_id: Mapped[str] = mapped_column(String(64))
    answers_json: Mapped[str] = mapped_column(Text)  # collected clarifying-question answers
    revision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # appended on each ❌
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[ContentStatus] = mapped_column(SAEnum(ContentStatus), default=ContentStatus.draft)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["Account"] = relationship(back_populates="generation_jobs")


class AuditLogEntry(Base):
    """Who approved/published/redid what, and when — doc §7."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)  # see ChatBinding note
    role: Mapped[Role] = mapped_column(SAEnum(Role))
    action: Mapped[str] = mapped_column(String(255))  # "approved", "published", "requested_redo"
    content_item_id: Mapped[int | None] = mapped_column(ForeignKey("content_items.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
