"""
ElevenLabs adapter — one fixed voice_id per blogger persona for consistency
across all her Reels (doc §4.6).
"""
import httpx

from config import settings

BASE_URL = "https://api.elevenlabs.io/v1"


class ElevenLabsClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
        )

    async def synthesize(self, text: str, voice_id: str) -> bytes:
        """Returns raw audio bytes (mp3) for the given voiceover text."""
        # TODO: POST /text-to-speech/{voice_id}
        raise NotImplementedError
