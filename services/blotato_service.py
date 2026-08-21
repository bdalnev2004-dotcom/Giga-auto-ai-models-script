"""
Blotato adapter — TikTok cross-posting.

OUT OF SCOPE for now: posting is Instagram Reels only (per clarification).
Kept as a stub in case TikTok cross-posting gets added back later — nothing
in approvals.py currently routes to this.
"""
import httpx

from config import settings

BASE_URL = "https://backend.blotato.com/v2"


class BlotatoClient:
    def __init__(self, tiktok_account_id: str):
        self.tiktok_account_id = tiktok_account_id
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"blotato-api-key": settings.BLOTATO_API_KEY},
        )

    async def post_video(self, video_path: str, caption: str) -> dict:
        # TODO: wire up Blotato post endpoint for this tiktok_account_id
        raise NotImplementedError
