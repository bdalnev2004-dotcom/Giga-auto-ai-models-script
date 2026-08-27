"""
The generation layer.

Rewritten around three problems the first version had:

1. ONE PROMPT FOR EVERYTHING. A bio and a Reels script were built from the same
   generic instruction with a stringified dict pasted in. Craft now lives in
   services/prompts.py, one brief per scenario.

2. NO VARIANTS. The bot asked "номер варианта / ✅ / ❌" but generation returned a
   single blob — there was nothing to number. `generate_variants` returns a real
   numbered set, shape-guaranteed by structured outputs rather than parsed out of prose.

3. NO PERSONA DEPTH. `persona_summary` was a "; ".join of interview answers. It is
   now a structured PersonaCard, and for images the appearance block is reused
   verbatim — that is what keeps an AI blogger looking like the same person.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from config import settings
from services.persona import PersonaCard
from services import prompts, feedback

client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


@dataclass(frozen=True)
class Variant:
    number: int
    angle: str      # what makes this option different — shown so the choice is informed
    text: str

    def render(self) -> str:
        return f"<b>{self.number}. {self.angle}</b>\n{self.text}"


class GenerationError(RuntimeError):
    pass


def ask_next_question(
    scenario_id: str, question_bank: list[str], answers_so_far: dict
) -> str | None:
    """
    Next unanswered question from the scenario's bank, or None when it is exhausted.

    Deliberately local and synchronous — no model call. The previous docstring
    claimed Claude decided what to ask; it never did, and an API round-trip per
    question would only add latency to a fixed list.
    """
    remaining = [q for q in question_bank if q not in answers_so_far]
    return remaining[0] if remaining else None


async def generate_variants(
    scenario_id: str,
    persona: PersonaCard,
    answers: dict,
    revision_notes: str | None = None,
    previous_attempt: str | None = None,
    account_id: int | None = None,
) -> list[Variant]:
    """
    Produces the numbered options the approval flow expects.

    revision_notes / previous_attempt carry a ❌ forward within one dialog, so the
    retry fixes the stated complaint instead of re-rolling the same idea.
    account_id additionally pulls in what this account has taught the system across
    all previous dialogs — approved examples and complaints made more than once.
    """
    learned = feedback.render_for_prompt(
        await feedback.learned_context(account_id, scenario_id)
    )
    system = prompts.build_system_prompt(persona, scenario_id, learned)
    user = prompts.build_user_prompt(scenario_id, answers, revision_notes, previous_attempt)

    try:
        response = await client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=16000,
            # `thinking` is deliberately not passed: on claude-opus-5 adaptive
            # thinking is already the default, so omitting it gets the same
            # behaviour without depending on the installed SDK knowing the param.
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "format": {"type": "json_schema", "schema": prompts.VARIANTS_SCHEMA}
            },
        )
    except Exception as e:
        raise GenerationError(f"Claude не ответил: {type(e).__name__}: {e}") from e

    if response.stop_reason == "refusal":
        raise GenerationError("Claude отказался выполнять запрос.")

    try:
        text = next(b.text for b in response.content if b.type == "text")
        payload = json.loads(text)
    except (StopIteration, json.JSONDecodeError) as e:
        raise GenerationError(f"Ответ не разобрался как JSON: {e}") from e

    variants = [
        Variant(number=v["number"], angle=v["angle"], text=v["text"])
        for v in payload.get("variants", [])
    ]
    if not variants:
        raise GenerationError("Claude вернул пустой список вариантов.")

    # Renumber defensively: the approval mechanic maps a typed number to a position,
    # so a gap or a duplicate in the model's numbering would approve the wrong item.
    return [
        Variant(number=i, angle=v.angle, text=v.text)
        for i, v in enumerate(variants, start=1)
    ]


def render_variants(variants: list[Variant]) -> str:
    """Chat-ready block. Kept here so every scenario presents choices identically."""
    body = "\n\n".join(v.render() for v in variants)
    return f"{body}\n\nЧто берём? (номер / несколько номеров через запятую / ❌ с замечанием)"


async def build_persona_card(raw_answers: dict[str, str]) -> PersonaCard:
    """
    Turns interview answers into a filled card, asking Claude to expand the thin
    parts — especially the appearance lock, where a one-line answer ("блондинка, 25")
    is not enough to hold a face steady across hundreds of generations.
    """
    from services.persona import card_from_answers

    card = card_from_answers(raw_answers)
    if not card.appearance_lock:
        return card

    system = (
        "Ты помогаешь собрать карточку AI-персонажа для Instagram. "
        "Твоя задача — превратить черновые ответы в точное описание внешности, "
        "по которому генератор изображений будет раз за разом выдавать одного и того же "
        "человека. Описывай только устойчивые черты: форма лица, черты, цвет и текстура "
        "волос, глаза, брови, телосложение, рост, тон кожи, особые приметы. "
        "Не описывай одежду, позу, эмоцию и фон — они меняются от кадра к кадру. "
        "Пиши сплошным текстом на английском, без списков и заголовков."
    )
    user = (
        f"Черновое описание внешности: {card.appearance_lock}\n"
        f"Возраст: {card.age or 'не указан'}. Город: {card.city or 'не указан'}.\n\n"
        "Разверни это в детальное устойчивое описание внешности."
    )

    try:
        response = await client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        expanded = next(b.text for b in response.content if b.type == "text").strip()
        if expanded:
            card.appearance_lock = expanded
    except Exception:
        # Keep the operator's own wording rather than failing account creation —
        # a thin lock still beats no account.
        pass

    return card
