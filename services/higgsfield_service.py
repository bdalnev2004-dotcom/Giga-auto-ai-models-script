"""
Higgsfield: photos, logos and raw video.

THE POINT OF THIS MODULE IS FACE CONSISTENCY. A text description alone will not
hold a face steady — prompt the same appearance twice and you get two different
women. Higgsfield's Soul family gives three tiers, and the account should climb
them in this order:

  1. soul/standard   — text only. Used ONCE, to cast the character: generate a
                       batch, pick the best frame, that frame becomes the anchor.
  2. soul/reference  — anchor image + prompt. Every day-to-day photo goes through
                       here, so each shot is pulled back toward the same face.
  3. soul/character  — a trained custom_reference_id. Strongest consistency.
                       The id is created in the Higgsfield product, not here (the
                       API exposes no endpoint to train one), then pasted onto the
                       account.

The API is asynchronous everywhere: POST returns a request_id, then you poll
until it completes. Auth is a KEY PAIR — id and secret — not a single token.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx

from config import settings

BASE_URL = "https://api.higgsfield.ai"

POLL_INTERVAL_SECONDS = 3
POLL_TIMEOUT_SECONDS = 600  # image batches are quick; video can run for minutes

# Reels geometry. Everything the farm publishes is vertical.
VERTICAL = "9:16"


class HiggsfieldError(RuntimeError):
    pass


@dataclass(frozen=True)
class Job:
    request_id: str
    urls: list[str]


class HiggsfieldClient:
    def __init__(self, key_id: str | None = None, key_secret: str | None = None):
        key_id = key_id or settings.HIGGSFIELD_API_KEY_ID
        key_secret = key_secret or settings.HIGGSFIELD_API_KEY_SECRET
        if not key_id or not key_secret:
            raise HiggsfieldError(
                "Нужны HIGGSFIELD_API_KEY_ID и HIGGSFIELD_API_KEY_SECRET — "
                "у Higgsfield авторизация парой значений, одного ключа мало."
            )
        self._auth = f"Key {key_id}:{key_secret}"

    # ----------------------------------------------------------------- core --
    async def _post(self, path: str, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{BASE_URL}{path}",
                headers={"Authorization": self._auth, "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code in (401, 403):
            raise HiggsfieldError(f"Higgsfield отклонил ключи ({response.status_code}).")
        if response.status_code >= 400:
            raise HiggsfieldError(f"{path} вернул {response.status_code}: {response.text[:300]}")

        request_id = response.json().get("request_id")
        if not request_id:
            raise HiggsfieldError(f"В ответе нет request_id: {response.text[:300]}")
        return request_id

    async def _await_result(self, request_id: str) -> list[str]:
        """Polls until the job finishes; returns the output file URLs."""
        waited = 0
        async with httpx.AsyncClient(timeout=30) as client:
            while waited < POLL_TIMEOUT_SECONDS:
                response = await client.get(
                    f"{BASE_URL}/requests/{request_id}/status",
                    headers={"Authorization": self._auth},
                )
                if response.status_code >= 400:
                    raise HiggsfieldError(
                        f"Статус {request_id}: {response.status_code} {response.text[:200]}"
                    )
                data = response.json()
                status = (data.get("status") or "").lower()

                if status in {"completed", "succeeded", "success"}:
                    urls = _extract_urls(data)
                    if not urls:
                        raise HiggsfieldError(f"Задача {request_id} завершилась без файлов.")
                    return urls
                if status in {"failed", "error", "canceled", "cancelled"}:
                    raise HiggsfieldError(
                        f"Задача {request_id} завершилась со статусом «{status}»: "
                        f"{data.get('error') or data.get('message') or 'без деталей'}"
                    )

                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                waited += POLL_INTERVAL_SECONDS

        raise HiggsfieldError(
            f"Задача {request_id} не завершилась за {POLL_TIMEOUT_SECONDS // 60} мин."
        )

    async def _run(self, path: str, payload: dict) -> Job:
        request_id = await self._post(path, payload)
        return Job(request_id=request_id, urls=await self._await_result(request_id))

    # ---------------------------------------------------------------- photo --
    async def cast_character(
        self, appearance_prompt: str, num_images: int = 4, resolution: str = "2K"
    ) -> Job:
        """
        Step 1: cast the persona. Text-only generation from the appearance lock,
        run once per account. Pick the best frame from the batch and store it as
        the anchor — everything after this references it.
        """
        return await self._run(
            "/higgsfield-ai/soul/standard",
            {
                "prompt": appearance_prompt,
                "num_images": max(1, min(num_images, 4)),
                "resolution": resolution,
                "aspect_ratio": VERTICAL,
            },
        )

    async def generate_photo(
        self,
        prompt: str,
        anchor_image_url: str | None = None,
        custom_reference_id: str | None = None,
        reference_strength: float = 0.7,
        batch_size: int = 4,
        seed: int | None = None,
    ) -> Job:
        """
        Step 2/3: a day-to-day photo of an already-cast persona.

        Routes to the strongest consistency mechanism available: a trained
        custom_reference_id if the account has one, otherwise the anchor frame.
        Falling through to text-only is refused rather than silently producing a
        stranger with the right hair colour.
        """
        if custom_reference_id:
            payload = {
                "prompt": prompt,
                "custom_reference_id": custom_reference_id,
                "custom_reference_strength": reference_strength,
                "batch_size": 4 if batch_size > 1 else 1,
                "resolution": "1080p",
            }
            if anchor_image_url:
                payload["image_reference_url"] = anchor_image_url
            if seed is not None:
                payload["seed"] = seed
            return await self._run("/higgsfield-ai/soul/character", payload)

        if anchor_image_url:
            payload = {
                "prompt": prompt,
                "image_reference_url": anchor_image_url,
                "batch_size": 4 if batch_size > 1 else 1,
                "resolution": "1080p",
                "aspect_ratio": VERTICAL,
            }
            if seed is not None:
                payload["seed"] = seed
            return await self._run("/higgsfield-ai/soul/reference", payload)

        raise HiggsfieldError(
            "Нет ни anchor_image_url, ни custom_reference_id. Сначала прогони "
            "cast_character и закрепи за аккаунтом эталонный кадр — иначе на каждом "
            "фото будет другой человек."
        )

    async def generate_logo(self, spec_prompt: str, num_images: int = 4) -> Job:
        """Logos have no face to hold — plain text-to-image is the right tool."""
        return await self._run(
            "/higgsfield-ai/soul/standard",
            {
                "prompt": spec_prompt,
                "num_images": max(1, min(num_images, 4)),
                "resolution": "2K",
                "aspect_ratio": "1:1",
            },
        )

    # ---------------------------------------------------------------- video --
    async def animate_photo(
        self,
        image_url: str,
        prompt: str,
        duration: int = 5,
        model: str = "kling",
        with_audio: bool = False,
    ) -> Job:
        """
        Raw footage for a reel: animates an already-approved photo, which keeps
        the persona's face in the video for the same reason it keeps it in stills.

        kling  — image-to-video, 5 or 10 seconds.
        veo    — 4/6/8 seconds, can generate its own audio.
        """
        if model == "veo":
            return await self._run(
                "/veo3.1/fast/image-to-video",
                {
                    "prompt": prompt,
                    "image_url": image_url,
                    "duration": duration if duration in (4, 6, 8) else 6,
                    "resolution": 1080,
                    "aspect_ratio": VERTICAL,
                    "generate_audio": with_audio,
                },
            )
        return await self._run(
            "/kling-video/v2.5-turbo/pro/image-to-video",
            {
                "prompt": prompt,
                "image_url": image_url,
                "duration": 10 if duration > 5 else 5,
                "cfg_scale": 0.5,
            },
        )

    # ----------------------------------------------------------------- misc --
    async def download(self, url: str, dst: str | Path) -> Path:
        """Output files expire after about a week — pull them down immediately."""
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            response = await client.get(url)
        if response.status_code != 200:
            raise HiggsfieldError(f"Не скачался результат: {response.status_code}")
        dst.write_bytes(response.content)
        return dst


def _extract_urls(data: dict) -> list[str]:
    """
    Pulls file URLs out of the status payload. The shape varies by model family,
    so this looks in the documented places rather than assuming one of them.
    """
    urls: list[str] = []
    for key in ("results", "output", "outputs", "files"):
        value = data.get(key)
        if isinstance(value, str):
            urls.append(value)
        elif isinstance(value, dict):
            urls += [v for v in value.values() if isinstance(v, str) and v.startswith("http")]
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and entry.startswith("http"):
                    urls.append(entry)
                elif isinstance(entry, dict):
                    urls += [
                        v for k, v in entry.items()
                        if k in ("url", "file_url", "image_url", "video_url")
                        and isinstance(v, str)
                    ]
    return list(dict.fromkeys(urls))
