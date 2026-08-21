"""
HikerAPI adapter — the private-API route for everything the official Meta Graph API
can't do automatically (clickable story links). Doc §3.6 / §5.1.

Per-account creds (username, password, totp_secret, session_file, proxy) come from
the vault, decrypted here right before use — never earlier in the pipeline.
"""
import httpx

from config import settings

BASE_URL = "https://api.hikerapi.com"


class HikerAPIClient:
    def __init__(self, session_file: str, proxy: str):
        # The "personality" of an IG account = session_file + pinned proxy — must
        # never be swapped between accounts (doc §4 callout).
        self.session_file = session_file
        self.proxy = proxy
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"x-access-key": settings.HIKERAPI_ACCESS_KEY},
        )

    async def post_photo(self, image_path: str, caption: str) -> dict:
        # TODO: wire up actual HikerAPI photo-upload endpoint + session/proxy binding
        raise NotImplementedError

    async def post_reel(self, video_path: str, caption: str, share_to_feed: bool = True) -> dict:
        # TODO: reel upload; share_to_feed=False handles the "repost without cluttering
        # the profile grid" case from doc §3.8
        raise NotImplementedError

    async def post_story_with_link(self, video_or_image_path: str, link_url: str, link_label: str) -> dict:
        # TODO: StoryLink sticker — this is the whole reason HikerAPI was chosen
        # over the official Graph API (doc §3.6)
        raise NotImplementedError
