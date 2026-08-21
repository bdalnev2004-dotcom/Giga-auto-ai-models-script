"""
Vyra is integrated via MCP (doc §3.8), so this adapter is a thin MCP tool-call
wrapper rather than a plain REST client. Assembles raw footage + voiceover/music +
subtitles + hook text, optionally against the account's stored edit-style guide
from /15_шаблон_монтажа/.
"""


class VyraClient:
    def __init__(self, mcp_url: str):
        self.mcp_url = mcp_url

    async def assemble_reel(
        self,
        raw_video_path: str,
        voiceover_path: str | None,
        music_track: str | None,
        subtitles: bool,
        hook_text: str | None,
        style_guide_text: str | None,
        reference_video_path: str | None = None,
    ) -> str:
        # TODO: call Vyra MCP tool(s); returns path to the finished edited reel
        raise NotImplementedError
