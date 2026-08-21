"""
Higgsfield adapter — logos, highlight covers, daily photos, raw reel footage.
Consistent face across generations (doc's flagged risk in §4.1) needs a fixed
reference face-set from /01_персонаж/ passed as conditioning input on every call.
"""
import httpx

from config import settings

BASE_URL = "https://api.higgsfield.ai"  # placeholder — confirm actual base URL


class HiggsfieldClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {settings.HIGGSFIELD_API_KEY}"},
        )

    async def generate_logo(self, brand_name: str, style_brief: dict, transparent_bg: bool = True) -> list[str]:
        # TODO: returns list of local file paths / URLs for logo_1..N
        raise NotImplementedError

    async def generate_photo(self, persona_reference_images: list[str], scene_brief: dict) -> str:
        # TODO: pass reference face-set for consistency; returns one image path
        raise NotImplementedError

    async def generate_reel_raw(self, script_text: str, persona_reference_images: list[str] | None) -> str:
        # TODO: returns path to raw (pre-Vyra) video
        raise NotImplementedError
