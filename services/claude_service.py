"""
Wraps the Anthropic API call that plays the role of "Claude inside the bot" from
the doc: knows the scenario, asks only the missing clarifying questions, writes
copy/scripts, and decides when a scenario is ready to hand off to another service.
"""
from anthropic import AsyncAnthropic

from config import settings

client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are the orchestration brain for a social-media account farm bot (Russian-language).
You run one scenario at a time, scoped to a single account. Ask only the clarifying
questions that scenario genuinely needs (see the scenario's question bank), pulling
answers from the account card wherever possible instead of re-asking.
When you have enough information, produce the requested content (copy, script text,
carousel copy, etc.) or state that it's ready to hand off to an external generation
service (Higgsfield / ElevenLabs / Vyra / HikerAPI).
Keep replies short and in Russian, matching the tone of the account's persona.
"""


async def ask_next_question(scenario_id: str, question_bank: list[str], answers_so_far: dict) -> str | None:
    """Returns the next unanswered question, or None if the bank is exhausted."""
    remaining = [q for q in question_bank if q not in answers_so_far]
    return remaining[0] if remaining else None


async def generate_copy(
    scenario_id: str, account_context: dict, answers: dict, revision_notes: str | None = None
) -> str:
    """
    Calls Claude to produce the actual text output (bio, script, caption, ...).
    account_context should include persona_summary, niche, voice/tone, etc.
    revision_notes: set when regenerating after a ❌ — the person's freeform
    complaint about the previous batch, appended so the next attempt actually
    fixes what was wrong instead of blindly repeating itself.
    """
    prompt = (
        f"Сценарий: {scenario_id}\n"
        f"Контекст аккаунта: {account_context}\n"
        f"Собранные ответы: {answers}\n"
    )
    if revision_notes:
        prompt += (
            f"\nПредыдущий вариант отклонили с комментарием: «{revision_notes}».\n"
            "Учти это замечание и не повторяй прежнюю ошибку.\n"
        )
    prompt += "\nСгенерируй финальный текст для этого сценария."
    response = await client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=1500,
        system=ORCHESTRATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
