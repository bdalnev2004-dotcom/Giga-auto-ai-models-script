"""
Trigger dictionary: keyword/phrase -> scenario id.
One keyword must map unambiguously to one scenario; synonyms listed so the
person doesn't need to memorize exact wording. Extend freely.

Scenario ids ending in nothing = top-level (creates a whole account).
Everything else = a single step inside the currently selected /account.
"""

# --- Account context switching (must run before any other trigger if >1 account) ---
ACCOUNT_SWITCH_TRIGGERS = {
    "account",
    "аккаунт",
    "переключись на",
}

# --- Top-level scenarios: create a whole account ---
TOP_LEVEL_SCENARIOS = {
    "create_brand": ["создать бренд", "создать компанию", "новый бренд"],
    "create_blogger": ["создать блогершу", "новая блогерша", "создать ai"],
}

# --- Single-step scenarios inside an already-created account ---
#
# File operations (uniquify) are deliberately NOT here: they live in
# handlers/media.py, registered ahead of the generic runner. Routing them through
# this dictionary would hand a video task to the copywriting path.
STEP_SCENARIOS = {
    "brand_name": {
        "triggers": ["название", "нейм", "имя"],
        "service": "claude",
    },
    "bio": {
        "triggers": ["био", "описание"],
        "service": "claude",
    },
    "logo": {
        "triggers": ["лого", "логотип"],
        "service": "higgsfield",
    },
    "highlight_covers": {
        "triggers": ["обложки закрепа", "хайлайты", "иконки закрепа"],
        "service": "claude",  # generation via Higgsfield, copy/spec via Claude
    },
    "story_covers": {
        "triggers": ["сторис закрепа", "баннеры закрепа"],
        "service": "claude",
    },
    "daily_photo": {
        "triggers": ["фото", "сгенерь фото"],
        "service": "higgsfield",
    },
    "reels_scripts": {
        "triggers": ["сценарии", "сценарии рилс"],
        "service": "claude",
    },
    "voiceover_text": {
        "triggers": ["озвучка текст", "voiceover текст", "текст голоса"],
        "service": "claude",
    },
    "voiceover_audio": {
        "triggers": ["озвучка", "голос", "voiceover"],
        "service": "elevenlabs",
    },
    "carousel": {
        "triggers": ["карусель", "карусели"],
        "service": "claude",  # script; rendering uses the account's HTML template
    },
    "reels_edit": {
        "triggers": ["собрать рилс", "монтаж", "доработать", "правка"],
        "service": "editor",  # local ffmpeg, see services/editor_service.py
    },
    "daily_story": {
        "triggers": ["сторис", "дневная сторис", "выложить сторис"],
        "service": "instagram",
    },
    "tg_post": {
        "triggers": ["тг пост", "пост в телеграм", "tgpost"],
        "service": "claude",
    },
}


def resolve_trigger(text: str) -> tuple[str | None, str | None]:
    """
    Match free-text input against the trigger dictionary.
    Returns (kind, scenario_id) where kind is 'account_switch' | 'top_level' | 'step' | None.
    """
    normalized = text.strip().lower()

    for word in ACCOUNT_SWITCH_TRIGGERS:
        if normalized.startswith(word):
            return "account_switch", None

    for scenario_id, phrases in TOP_LEVEL_SCENARIOS.items():
        if any(p in normalized for p in phrases):
            return "top_level", scenario_id

    for scenario_id, cfg in STEP_SCENARIOS.items():
        if any(p in normalized for p in cfg["triggers"]):
            return "step", scenario_id

    return None, None
