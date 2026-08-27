"""
ElevenLabs voiceover.

One fixed voice_id per blogger persona: the voice is part of who she is, and
swapping it between reels breaks the illusion as surely as swapping her face.
Account.voice_id holds it; pick the voice once and never rotate it.

eleven_multilingual_v2 is the model to use here — the scripts are Russian, and
the English-only models mangle Cyrillic.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from config import settings

BASE_URL = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_multilingual_v2"

# Reels voiceover wants a steady, predictable read, not dramatic range:
# higher stability, mild style. Tune per persona if a blogger needs more energy.
DEFAULT_VOICE_SETTINGS = {
    "stability": 0.55,
    "similarity_boost": 0.80,
    "style": 0.15,
    "use_speaker_boost": True,
    "speed": 1.0,
}


class VoiceoverError(RuntimeError):
    pass


class ElevenLabsClient:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or settings.ELEVENLABS_API_KEY
        if not self._api_key:
            raise VoiceoverError("ELEVENLABS_API_KEY не задан — озвучка недоступна.")

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        dst: str | Path,
        model_id: str = DEFAULT_MODEL,
        voice_settings: dict | None = None,
    ) -> Path:
        """Renders `text` with `voice_id` and writes the mp3 to `dst`."""
        if not text.strip():
            raise VoiceoverError("Пустой текст — нечего озвучивать.")
        if not voice_id:
            raise VoiceoverError(
                "У аккаунта не задан voice_id. Выбери голос в кабинете ElevenLabs "
                "и пропиши его в карточке — он должен быть один и тот же для всех роликов."
            )

        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": voice_settings or DEFAULT_VOICE_SETTINGS,
        }

        try:
            # Long scripts take a while to render; the default httpx timeout is
            # far too short for anything past a couple of sentences.
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    f"{BASE_URL}/text-to-speech/{voice_id}",
                    headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.HTTPError as e:
            raise VoiceoverError(f"Сеть до ElevenLabs: {type(e).__name__}: {e}") from e

        if response.status_code == 401:
            raise VoiceoverError("ElevenLabs отклонил ключ (401).")
        if response.status_code == 422:
            raise VoiceoverError(f"ElevenLabs не принял запрос: {response.text[:300]}")
        if response.status_code != 200:
            raise VoiceoverError(f"ElevenLabs вернул {response.status_code}: {response.text[:300]}")

        dst.write_bytes(response.content)
        if dst.stat().st_size == 0:
            raise VoiceoverError("ElevenLabs вернул пустой файл.")
        return dst

    async def list_voices(self) -> list[dict]:
        """Available voices — used when picking the one to pin to a persona."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BASE_URL}/voices", headers={"xi-api-key": self._api_key}
            )
        if response.status_code != 200:
            raise VoiceoverError(f"Не удалось получить список голосов: {response.status_code}")
        return [
            {"voice_id": v["voice_id"], "name": v.get("name", ""), "labels": v.get("labels", {})}
            for v in response.json().get("voices", [])
        ]

    async def quota(self) -> dict:
        """Remaining characters — worth checking before a batch of 30 reels."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{BASE_URL}/user/subscription", headers={"xi-api-key": self._api_key}
            )
        if response.status_code != 200:
            raise VoiceoverError(f"Не удалось получить квоту: {response.status_code}")
        data = response.json()
        used, limit = data.get("character_count", 0), data.get("character_limit", 0)
        return {"tier": data.get("tier"), "used": used, "limit": limit, "left": limit - used}
