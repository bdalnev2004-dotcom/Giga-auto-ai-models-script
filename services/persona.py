"""
Character card — the backbone of "this AI blogger looks and sounds like one real person".

Two problems this solves, both of which the old `Account.persona_summary` (a
"; ".join() of raw interview answers) could not:

1. VISUAL CONSISTENCY. An AI influencer only reads as real if her face, build,
   hair and styling are identical in every photo. That comes from reusing one
   frozen appearance string, verbatim, in every image prompt — not from
   re-describing her each time and hoping. `appearance_lock` is that string, and
   nothing in the pipeline may paraphrase it.

2. VOICE CONSISTENCY. Tone, vocabulary and forbidden words have to reach the copy
   prompts as structured guidance, not as a blob the model has to re-interpret.

The card is stored as JSON on Account.persona_json so it can grow fields without
a migration per field, and is versioned so we can tell which cards predate a
prompt change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

CARD_VERSION = 1


@dataclass
class PersonaCard:
    # --- identity -----------------------------------------------------------
    display_name: str = ""
    age: int | None = None
    city: str | None = None
    occupation: str | None = None
    backstory: str = ""              # 2-3 sentences; gives the copy something to reference

    # --- the frozen visual identity ----------------------------------------
    # Written once, reused verbatim in EVERY image/video prompt. Editing it
    # mid-account changes who she looks like, so treat a change as a new persona.
    appearance_lock: str = ""
    wardrobe_notes: str = ""         # recurring style, not a single outfit
    setting_notes: str = ""          # the flats/cafes/streets she is usually shot in

    # --- voice --------------------------------------------------------------
    tone: str = ""                   # "дружелюбно, с самоиронией"
    vocabulary_notes: str = ""       # words she uses; slang level; formality
    signature_phrases: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)   # words/claims she never makes

    # --- commercial ---------------------------------------------------------
    niche: str = ""
    audience: str = ""               # who is watching, in their words
    products: str = ""               # what she reviews / what the brand sells
    cta_style: str = ""              # how she asks for the click

    version: int = CARD_VERSION

    # ------------------------------------------------------------------ io --
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str | None) -> "PersonaCard":
        if not raw:
            return cls()
        data = json.loads(raw)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    # -------------------------------------------------------------- prompts --
    def appearance_prompt(self) -> str:
        """
        The exact block handed to image/video generation. Deliberately returns the
        lock unchanged — callers append the scene, never rewrite the person.
        """
        if not self.appearance_lock:
            raise ValueError(
                f"У персоны «{self.display_name or '?'}» не заполнен appearance_lock — "
                "без него фото будут каждый раз с разным человеком."
            )
        parts = [self.appearance_lock]
        if self.wardrobe_notes:
            parts.append(f"Гардероб: {self.wardrobe_notes}")
        if self.setting_notes:
            parts.append(f"Типичная обстановка: {self.setting_notes}")
        return "\n".join(parts)

    def voice_guidance(self) -> str:
        """Compact voice brief injected into every copy prompt."""
        lines: list[str] = []
        who = ", ".join(
            str(x) for x in [self.display_name or None, self.age, self.city, self.occupation] if x
        )
        if who:
            lines.append(f"Кто говорит: {who}.")
        if self.backstory:
            lines.append(f"Бэкграунд: {self.backstory}")
        if self.tone:
            lines.append(f"Тон: {self.tone}")
        if self.vocabulary_notes:
            lines.append(f"Лексика: {self.vocabulary_notes}")
        if self.signature_phrases:
            lines.append("Свои словечки (использовать умеренно, не в каждом тексте): "
                         + ", ".join(f"«{p}»" for p in self.signature_phrases))
        if self.audience:
            lines.append(f"Аудитория: {self.audience}")
        if self.cta_style:
            lines.append(f"Как зовёт к действию: {self.cta_style}")
        if self.forbidden:
            lines.append("Никогда не использует: " + ", ".join(self.forbidden))
        return "\n".join(lines) if lines else "Персона не заполнена — пиши нейтрально."

    def missing_fields(self) -> list[str]:
        """What still has to be filled before this account can generate properly."""
        required = {
            "display_name": "имя",
            "appearance_lock": "внешность (критично для фото)",
            "tone": "тон",
            "niche": "ниша",
            "audience": "аудитория",
        }
        return [label for attr, label in required.items() if not getattr(self, attr)]


# The interview that produces a card. Kept separate from the generic QUESTION_BANK
# because a persona is built once per account and deserves real questions —
# vague answers here degrade every generation that follows.
PERSONA_INTERVIEW: list[tuple[str, str]] = [
    ("display_name", "Имя блогерши (как её зовут подписчики)?"),
    ("age", "Возраст?"),
    ("city", "Город — откуда она и где снимается?"),
    ("appearance_lock",
     "Внешность максимально подробно: тип лица, цвет и длина волос, глаза, телосложение, "
     "рост, кожа, особые приметы. Это описание зафиксируется и пойдёт в КАЖДУЮ генерацию "
     "фото — чем детальнее, тем стабильнее будет лицо."),
    ("wardrobe_notes", "Как она обычно одета? (стиль в целом, не один образ)"),
    ("setting_notes", "Где её обычно снимают — интерьеры, улицы, студия?"),
    ("occupation", "Чем занимается по легенде?"),
    ("backstory", "Короткая история: откуда, к чему идёт, что для неё важно (2-3 предложения)"),
    ("tone", "Тон общения: дружелюбно / экспертно / дерзко / с самоиронией?"),
    ("vocabulary_notes", "Лексика: сленг, «ты» или «вы», эмодзи?"),
    ("niche", "Ниша?"),
    ("audience", "Кто её смотрит — возраст, интересы, боль?"),
    ("products", "Какие товары обозревает?"),
    ("cta_style", "Как зовёт к действию — мягко, прямо, через интригу?"),
    ("forbidden", "Что она никогда не говорит/не делает? (через запятую, можно пропустить)"),
]

LIST_FIELDS = {"signature_phrases", "forbidden"}
INT_FIELDS = {"age"}


def card_from_answers(answers: dict[str, str]) -> PersonaCard:
    """Builds a card from {field_name: raw answer} collected by the interview."""
    card = PersonaCard()
    for key, raw in answers.items():
        if key not in PersonaCard.__dataclass_fields__ or not raw:
            continue
        value: object = raw.strip()
        if key in LIST_FIELDS:
            value = [p.strip() for p in raw.split(",") if p.strip()]
        elif key in INT_FIELDS:
            try:
                value = int("".join(c for c in raw if c.isdigit()) or 0) or None
            except ValueError:
                value = None
        setattr(card, key, value)
    return card


# A brand has a voice but no face: skip every appearance field, keep the
# commercial and tone ones. Same card shape, so downstream code is identical.
BRAND_INTERVIEW: list[tuple[str, str]] = [
    ("display_name", "Название бренда (если ещё нет — напиши «нет», придумаем отдельно)"),
    ("niche", "Ниша и категория товаров?"),
    ("products", "Что продаёте — конкретно?"),
    ("audience", "Кто покупает: возраст, ситуация, боль?"),
    ("tone", "Тон бренда: дружелюбный / экспертный / премиальный / дерзкий?"),
    ("vocabulary_notes", "Как обращаетесь к клиенту — на «ты» или на «вы»? Эмодзи уместны?"),
    ("backstory", "Что за бренд, откуда, чем отличается от соседей по полке (2-3 предложения)"),
    ("cta_style", "Как зовёте к действию — мягко, прямо, через выгоду?"),
    ("setting_notes", "В какой обстановке снимается товар — студия, интерьер, улица?"),
    ("forbidden", "Что бренд никогда не говорит? (через запятую, можно пропустить)"),
]


def interview_for(account_type: str) -> list[tuple[str, str]]:
    """`create_brand` → BRAND_INTERVIEW, `create_blogger` → PERSONA_INTERVIEW."""
    return BRAND_INTERVIEW if account_type == "brand" else PERSONA_INTERVIEW
