"""
Central config. Loads everything from .env — never hardcode secrets here.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

    # Claude
    ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-opus-5")

    # DB
    DATABASE_URL: str = os.environ["DATABASE_URL"]

    # Redis — required in prod for FSM state to survive restarts (see bot.py::_build_storage)
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Vault
    VAULT_ENCRYPTION_KEY: str = os.environ["VAULT_ENCRYPTION_KEY"]

    # Google Drive
    GOOGLE_SERVICE_ACCOUNT_JSON: str = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    GOOGLE_DRIVE_ROOT_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")

    # Global platform keys (per doc section 4: these are farm-wide, not per-account)
    BLOTATO_API_KEY: str = os.getenv("BLOTATO_API_KEY", "")
    # Read-only API (analytics/references). Publishing runs through instagrapi.
    HIKERAPI_ACCESS_KEY: str = os.getenv("HIKERAPI_ACCESS_KEY", "")
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    HIGGSFIELD_API_KEY: str = os.getenv("HIGGSFIELD_API_KEY", "")
    VYRA_MCP_URL: str = os.getenv("VYRA_MCP_URL", "")

    TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow")


settings = Settings()

# Numbered Drive folder structure — from Strategy doc §7 / §6.4.
# Keep this as the single source of truth; every account gets these subfolders on creation.
DRIVE_FOLDER_TEMPLATE = [
    "01_персонаж",
    "02_аватар",
    "03_фото",
    "04_карусели/шаблон",
    "04_карусели/сценарии",
    "05_reels_сырые",
    "06_reels_монтаж",
    "07_озвучка",
    "08_reels_сценарии",
    "09_highlights",
    "10_сторис",
    "11_тексты",
    "12_telegram",
    "13_референсы",
    "14_опубликовано",
    "15_шаблон_монтажа",
    "16_уникализировано",
]

# Target approved-content counts per account (doc §9) — used to drive progress bars / reminders.
CONTENT_TARGETS = {
    "photos": 30,
    "bio": 1,
    "reels_scripts": 30,
    "voiceover_text": 30,
    "reels": 30,
    "carousel_scripts": 15,
    "carousels": 15,
}
