"""
The learning loop: what makes generation get better instead of repeating itself.

Before this module, the farm produced two valuable signals per task and discarded
both. A rejection note steered exactly one retry and was then dropped with the
dialog state; an approved text was never stored at all. Every new request started
from the same static brief and re-made the same mistakes the operator had already
corrected a dozen times.

Two signals, fed back into every later prompt for the same account and scenario:

  APPROVED EXAMPLES carry tone, rhythm and the right level of concreteness —
  things instructions describe badly and examples convey exactly. This is the
  stronger of the two by a wide margin: models imitate far more reliably than
  they comply.

  RECURRING REJECTIONS become standing constraints. A complaint the operator has
  made more than once is not a one-off mood, it is a house rule nobody wrote down.

Scope is per (account, scenario). One blogger's voice must not leak into another's,
and a rule about bios has no business shaping Reels scripts.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select

from db.models import ContentItem, ContentStatus, GenerationJob
from db.session import get_session

# How many approved samples to show. More is not better: past three or four the
# model starts averaging them into something bland instead of catching the voice.
MAX_EXAMPLES = 3

# A complaint has to repeat before it becomes a rule — otherwise a one-off remark
# ("сегодня короче") ossifies into a permanent constraint.
MIN_REPEATS_FOR_RULE = 2

# scenario_id -> content_type used when filing approved text.
CONTENT_TYPE_FOR_SCENARIO = {
    "bio": "text",
    "brand_name": "text",
    "reels_scripts": "reel_script",
    "voiceover_text": "voiceover_text",
    "carousel": "carousel_script",
    "tg_post": "tg_post",
    "daily_story": "story",
    "logo": "text",
    "highlight_covers": "text",
    "story_covers": "text",
}


@dataclass
class LearnedContext:
    examples: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.examples and not self.rules


def content_type_for(scenario_id: str) -> str:
    return CONTENT_TYPE_FOR_SCENARIO.get(scenario_id, "text")


async def _file_content_item(
    account_id: int,
    scenario_id: str,
    text: str,
    status: ContentStatus,
    scheduled_for=None,
) -> None:
    """
    Numbering matches services/library.py: per (account, content_type), so the
    number in Drive, in chat and in the database stay the same thing.
    """
    content_type = content_type_for(scenario_id)

    async with get_session() as session:
        from sqlalchemy import func

        current = await session.scalar(
            select(func.max(ContentItem.sequence_number)).where(
                ContentItem.account_id == account_id,
                ContentItem.content_type == content_type,
            )
        )
        session.add(
            ContentItem(
                account_id=account_id,
                content_type=content_type,
                sequence_number=(current or 0) + 1,
                body=text.strip(),
                status=status,
                scheduled_for=scheduled_for,
            )
        )
        await session.commit()


async def remember_approved(account_id: int, scenario_id: str, text: str) -> None:
    """Files an approved text so later generations can imitate it."""
    if not text or not text.strip():
        return
    await _file_content_item(account_id, scenario_id, text, ContentStatus.approved)


async def remember_scheduled(account_id: int, scenario_id: str, text: str, scheduled_for) -> None:
    """
    Files a text approved for later publication (the 📅 flow in approvals.py).

    Writing the row is the whole of what this does — nothing currently reads
    ContentStatus.scheduled rows back out and publishes them at the right time.
    scheduler.py::publish_daily_story is a documented stub; wiring it to actually
    consume these rows is separate, tracked work, not implied by this function
    existing.
    """
    if not text or not text.strip():
        return
    await _file_content_item(account_id, scenario_id, text, ContentStatus.scheduled, scheduled_for)


async def learned_context(account_id: int | None, scenario_id: str) -> LearnedContext:
    """Approved examples plus rules distilled from repeated complaints."""
    if account_id is None:
        return LearnedContext()

    content_type = content_type_for(scenario_id)
    async with get_session() as session:
        examples = (
            await session.execute(
                select(ContentItem.body)
                .where(
                    ContentItem.account_id == account_id,
                    ContentItem.content_type == content_type,
                    ContentItem.status == ContentStatus.approved,
                    ContentItem.body.is_not(None),
                )
                .order_by(ContentItem.created_at.desc())
                .limit(MAX_EXAMPLES)
            )
        ).scalars().all()

        complaints = (
            await session.execute(
                select(GenerationJob.revision_notes)
                .where(
                    GenerationJob.account_id == account_id,
                    GenerationJob.scenario_id == scenario_id,
                    GenerationJob.revision_notes.is_not(None),
                )
                .order_by(GenerationJob.created_at.desc())
                .limit(50)
            )
        ).scalars().all()

    return LearnedContext(
        examples=[e for e in examples if e],
        rules=_distil_rules(list(complaints)),
    )


def _normalise(note: str) -> str:
    """Crude stem so «слишком сухо» and «Слишком сухо!» count as the same complaint."""
    text = re.sub(r"[^\w\s]", " ", note.lower())
    return re.sub(r"\s+", " ", text).strip()


def _distil_rules(notes: list[str]) -> list[str]:
    """
    Complaints seen more than once become standing rules.

    Matching is on the whole normalised note rather than on keywords: a keyword
    approach turns "не надо про доставку" and "добавь про доставку" into the same
    rule, which is worse than having no rule.
    """
    counts = Counter(_normalise(n) for n in notes if n and n.strip())
    repeated = [note for note, count in counts.most_common() if count >= MIN_REPEATS_FOR_RULE]
    return repeated[:5]


def render_for_prompt(context: LearnedContext) -> str:
    """The block appended to the system prompt. Empty string when nothing is learned."""
    if context.is_empty():
        return ""

    parts: list[str] = []
    if context.examples:
        joined = "\n\n".join(f"— {e}" for e in context.examples)
        parts.append(
            "УЖЕ ОДОБРЕННОЕ ДЛЯ ЭТОГО АККАУНТА\n"
            "Ориентируйся на интонацию и уровень конкретики этих текстов. "
            "Не копируй их и не пересказывай — попадай в ту же манеру:\n"
            f"{joined}"
        )
    if context.rules:
        listed = "\n".join(f"- {r}" for r in context.rules)
        parts.append(
            "ЗАМЕЧАНИЯ, КОТОРЫЕ ЗАКАЗЧИК ПОВТОРЯЛ НЕ ОДИН РАЗ\n"
            "Это постоянные требования, а не разовые пожелания:\n"
            f"{listed}"
        )
    return "\n\n".join(parts)
