"""
Prompt builder: scenario briefs as editable, versioned data (doc §6, §10).

The briefs in services/prompts.py are the shipped defaults. This module lets them
be overridden from the bot without a redeploy — which the project needs, because
tuning briefs is the main lever on output quality and the operator works from
Telegram, not from a code editor.

Resolution order, most specific first:
    account override -> farm-wide override -> the default in prompts.BRIEFS

Every edit writes a new version rather than mutating the old one. A brief that
looked like an improvement and quietly made the copy worse can then be rolled
back — without history the only recovery is remembering what the text used to say.
"""
from __future__ import annotations

import json
from dataclasses import asdict, replace

from sqlalchemy import select

from db.models import PromptTemplate
from db.session import get_session
from services.prompts import BRIEFS, DEFAULT_BRIEF, Brief

# Fields an operator may edit. `variants` is an int; the rest are free text.
EDITABLE_FIELDS = ("goal", "constraints", "avoid", "quality_bar", "variants")

FIELD_LABELS = {
    "goal": "Задача",
    "constraints": "Формат и ограничения",
    "avoid": "Чего избегать",
    "quality_bar": "Критерий готовности",
    "variants": "Сколько вариантов",
}


class PromptStoreError(RuntimeError):
    pass


def default_brief(scenario_id: str) -> Brief:
    return BRIEFS.get(scenario_id, DEFAULT_BRIEF)


def _to_brief(payload: str) -> Brief:
    data = json.loads(payload)
    known = {f for f in Brief.__dataclass_fields__}
    return Brief(**{k: v for k, v in data.items() if k in known})


async def resolve(scenario_id: str, account_id: int | None = None) -> Brief:
    """The brief actually used for this (scenario, account)."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(PromptTemplate)
                .where(
                    PromptTemplate.scenario_id == scenario_id,
                    PromptTemplate.is_active.is_(True),
                    PromptTemplate.account_id.in_([account_id, None])
                    if account_id is not None
                    else PromptTemplate.account_id.is_(None),
                )
                .order_by(PromptTemplate.version.desc())
            )
        ).scalars().all()

    # Account-specific wins over farm-wide; both win over the shipped default.
    for row in sorted(rows, key=lambda r: (r.account_id is None, -r.version)):
        try:
            return _to_brief(row.payload_json)
        except (json.JSONDecodeError, TypeError):
            continue  # a corrupt row must not take the scenario down
    return default_brief(scenario_id)


async def save(
    scenario_id: str,
    field: str,
    value: str,
    account_id: int | None = None,
    edited_by: int | None = None,
) -> Brief:
    """
    Writes a new active version with one field changed, retiring the previous one.
    Returns the resulting brief.
    """
    if field not in EDITABLE_FIELDS:
        raise PromptStoreError(
            f"Поле «{field}» не редактируется. Доступны: {', '.join(EDITABLE_FIELDS)}."
        )

    current = await resolve(scenario_id, account_id)

    if field == "variants":
        try:
            parsed: object = max(1, min(int(value.strip()), 10))
        except ValueError as e:
            raise PromptStoreError("Количество вариантов — число от 1 до 10.") from e
    else:
        parsed = value.strip()
        if not parsed:
            raise PromptStoreError("Пустое значение — нечего сохранять.")

    updated = replace(current, **{field: parsed})

    async with get_session() as session:
        previous = (
            await session.execute(
                select(PromptTemplate).where(
                    PromptTemplate.scenario_id == scenario_id,
                    PromptTemplate.account_id.is_(account_id)
                    if account_id is None
                    else PromptTemplate.account_id == account_id,
                    PromptTemplate.is_active.is_(True),
                )
            )
        ).scalars().all()

        next_version = max((p.version for p in previous), default=0) + 1
        for row in previous:
            row.is_active = False

        session.add(
            PromptTemplate(
                account_id=account_id,
                scenario_id=scenario_id,
                payload_json=json.dumps(asdict(updated), ensure_ascii=False),
                version=next_version,
                is_active=True,
                edited_by=edited_by,
                note=f"{field}",
            )
        )
        await session.commit()

    return updated


async def history(scenario_id: str, account_id: int | None = None) -> list[dict]:
    """Version log, newest first — what to roll back to."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(PromptTemplate)
                .where(
                    PromptTemplate.scenario_id == scenario_id,
                    PromptTemplate.account_id.is_(None)
                    if account_id is None
                    else PromptTemplate.account_id == account_id,
                )
                .order_by(PromptTemplate.version.desc())
            )
        ).scalars().all()

    return [
        {
            "version": r.version,
            "active": r.is_active,
            "field": r.note,
            "created_at": r.created_at,
        }
        for r in rows
    ]


async def rollback(scenario_id: str, version: int, account_id: int | None = None) -> Brief:
    """Makes an earlier version active again."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(PromptTemplate).where(
                    PromptTemplate.scenario_id == scenario_id,
                    PromptTemplate.account_id.is_(None)
                    if account_id is None
                    else PromptTemplate.account_id == account_id,
                )
            )
        ).scalars().all()

        target = next((r for r in rows if r.version == version), None)
        if target is None:
            raise PromptStoreError(f"Версии {version} не существует.")

        for row in rows:
            row.is_active = row.version == version
        await session.commit()
        return _to_brief(target.payload_json)


async def reset(scenario_id: str, account_id: int | None = None) -> Brief:
    """Drops every override, returning to the brief shipped in the code."""
    async with get_session() as session:
        rows = (
            await session.execute(
                select(PromptTemplate).where(
                    PromptTemplate.scenario_id == scenario_id,
                    PromptTemplate.account_id.is_(None)
                    if account_id is None
                    else PromptTemplate.account_id == account_id,
                )
            )
        ).scalars().all()
        for row in rows:
            row.is_active = False
        await session.commit()
    return default_brief(scenario_id)


def render(brief: Brief, scenario_id: str, is_override: bool) -> str:
    """Human-readable brief for the chat."""
    source = "изменён" if is_override else "по умолчанию"
    return (
        f"<b>Бриф «{scenario_id}»</b> ({source})\n\n"
        f"<b>Задача</b>\n{brief.goal}\n\n"
        f"<b>Формат и ограничения</b>\n{brief.constraints}\n\n"
        f"<b>Чего избегать</b>\n{brief.avoid}\n\n"
        f"<b>Критерий готовности</b>\n{brief.quality_bar}\n\n"
        f"<b>Вариантов за раз:</b> {brief.variants}"
    )


async def is_overridden(scenario_id: str, account_id: int | None = None) -> bool:
    resolved = await resolve(scenario_id, account_id)
    return asdict(resolved) != asdict(default_brief(scenario_id))
