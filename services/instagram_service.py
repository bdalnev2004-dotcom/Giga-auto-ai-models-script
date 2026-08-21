"""
Instagram publishing via instagrapi (private API, run in-process).

HikerAPI is read-only — it exposes no upload endpoints — so publishing cannot go
through it. instagrapi runs the private API directly and is what provides the
StoryLink sticker that the official Graph API lacks (doc §3.6).

session_file + proxy together are the "personality" of an IG account and must
never be swapped between accounts (doc §4 callout).

instagrapi is synchronous, so every call is pushed off the event loop with
asyncio.to_thread — otherwise a single upload stalls the whole bot.
"""
import asyncio
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import LoginRequired
from instagrapi.types import StoryLink


class InstagramClient:
    def __init__(self, session_file: str, proxy: str = ""):
        self.session_file = Path(session_file)
        self._client = Client()
        if proxy:
            self._client.set_proxy(proxy)

    async def login(self, username: str, password: str, totp_secret: str | None = None) -> None:
        await asyncio.to_thread(self._login_sync, username, password, totp_secret)

    def _login_sync(self, username: str, password: str, totp_secret: str | None) -> None:
        if self.session_file.exists():
            self._client.load_settings(self.session_file)
            self._client.login(username, password)
            try:
                self._client.get_timeline_feed()
                return
            except LoginRequired:
                # Session expired. Keep the device fingerprint from the old session —
                # a fresh fingerprint on a known account looks like a hijack to Instagram.
                device = self._client.get_settings()["uuids"]
                self._client.set_settings({})
                self._client.set_uuids(device)

        verification_code = self._client.totp_generate_code(totp_secret) if totp_secret else ""
        self._client.login(username, password, verification_code=verification_code)

        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self._client.dump_settings(self.session_file)

    async def post_reel(self, video_path: str, caption: str, share_to_feed: bool = True) -> dict:
        media = await asyncio.to_thread(
            self._client.clip_upload,
            Path(video_path),
            caption,
            show_preview_in_feed=share_to_feed,
        )
        return media.dict()

    async def post_photo(self, image_path: str, caption: str) -> dict:
        media = await asyncio.to_thread(self._client.photo_upload, Path(image_path), caption)
        return media.dict()

    async def post_story_with_link(self, video_or_image_path: str, link_url: str, caption: str = "") -> dict:
        path = Path(video_or_image_path)
        upload = (
            self._client.video_upload_to_story
            if path.suffix.lower() == ".mp4"
            else self._client.photo_upload_to_story
        )
        story = await asyncio.to_thread(upload, path, caption, links=[StoryLink(webUri=link_url)])
        return story.dict()
