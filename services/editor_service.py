"""
Reel assembly on ffmpeg — the replacement for Vyra.

Vyra is unusable here: it authenticates through a browser OAuth flow and needs a
live editor session with the project open, and offers no server-side render. A
bot on a VPS assembling a reel at 20:00 with nobody watching cannot drive that.

ffmpeg does the same job headlessly: concatenate clips, lay the voiceover over
music at sane levels, burn in subtitles and a hook. The trade is that the edit
style is expressed as parameters here rather than described in words — which also
makes it reproducible, which matters when 20 accounts each want their own look.

Shares its subprocess plumbing with services/uniquify_service.py; the two run
back to back (assemble, then uniquify) and stay one dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from services.uniquify_service import FFMPEG, UniquifyError, _run

# Reels canvas. Anything not this shape gets padded into it rather than stretched.
WIDTH, HEIGHT = 1080, 1920
FPS = 30

# Music sits under speech, not beside it: -18 dB is roughly the point where a
# track supports a voice instead of fighting it.
MUSIC_DUCK_DB = -18.0


class EditError(UniquifyError):
    """Assembly failure. Subclasses UniquifyError so callers can catch one type."""


@dataclass
class Subtitle:
    start: float
    end: float
    text: str


@dataclass
class EditSpec:
    """
    One reel's edit. Per-account styling lives here — store it as JSON on the
    account and the look stays consistent without re-describing it every time.
    """
    clips: list[str] = field(default_factory=list)
    voiceover: str | None = None
    music: str | None = None
    hook_text: str | None = None
    subtitles: list[Subtitle] = field(default_factory=list)

    font_size: int = 56
    font_color: str = "white"
    outline_color: str = "black"
    hook_seconds: float = 2.0
    music_volume_db: float = MUSIC_DUCK_DB
    fade_seconds: float = 0.25


def _escape(text: str) -> str:
    """drawtext treats these as syntax — a stray colon silently kills the filter."""
    return (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def _fit_filter(index: int) -> str:
    """Scale into the canvas preserving aspect, pad the rest — never stretch faces."""
    return (
        f"[{index}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={FPS}[v{index}]"
    )


def _drawtext(text: str, spec: EditSpec, start: float | None, end: float | None, y: str) -> str:
    parts = [
        f"drawtext=text='{_escape(text)}'",
        f"fontsize={spec.font_size}",
        f"fontcolor={spec.font_color}",
        f"borderw=3:bordercolor={spec.outline_color}",
        f"x=(w-text_w)/2",
        f"y={y}",
        "line_spacing=8",
    ]
    if start is not None and end is not None:
        parts.append(f"enable='between(t,{start:.2f},{end:.2f})'")
    return ":".join(parts)


async def assemble(
    spec: EditSpec,
    dst: str | Path,
    crf: int = 20,
) -> Path:
    """
    Renders the reel described by `spec` to `dst`.

    Audio rules: with a voiceover present the music is ducked and both are mixed;
    with only music, the music carries the reel at full level; the clips' own
    audio is dropped either way, since raw AI footage rarely has usable sound.
    """
    if not spec.clips:
        raise EditError("Нет ни одного клипа — нечего собирать.")

    sources = [Path(c) for c in spec.clips]
    missing = [str(p) for p in sources if not p.exists()]
    if missing:
        raise EditError("Файлы не найдены: " + ", ".join(missing))

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = [FFMPEG, "-y"]
    for path in sources:
        args += ["-i", str(path)]

    voice_index = music_index = None
    if spec.voiceover:
        if not Path(spec.voiceover).exists():
            raise EditError(f"Озвучка не найдена: {spec.voiceover}")
        voice_index = len(sources)
        args += ["-i", spec.voiceover]
    if spec.music:
        if not Path(spec.music).exists():
            raise EditError(f"Музыка не найдена: {spec.music}")
        music_index = len(sources) + (1 if voice_index is not None else 0)
        args += ["-i", spec.music]

    # --- video graph -------------------------------------------------------
    chains = [_fit_filter(i) for i in range(len(sources))]
    concat_inputs = "".join(f"[v{i}]" for i in range(len(sources)))
    chains.append(f"{concat_inputs}concat=n={len(sources)}:v=1:a=0[base]")

    overlays: list[str] = []
    if spec.hook_text:
        overlays.append(_drawtext(spec.hook_text, spec, 0, spec.hook_seconds, "h*0.18"))
    for sub in spec.subtitles:
        overlays.append(_drawtext(sub.text, spec, sub.start, sub.end, "h*0.72"))
    if spec.fade_seconds > 0:
        overlays.append(f"fade=t=in:st=0:d={spec.fade_seconds:.2f}")

    chains.append(f"[base]{','.join(overlays)}[vout]" if overlays else "[base]copy[vout]")

    # --- audio graph -------------------------------------------------------
    audio_out = None
    if voice_index is not None and music_index is not None:
        chains.append(
            f"[{music_index}:a]volume={spec.music_volume_db:.1f}dB[bg];"
            f"[{voice_index}:a][bg]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        audio_out = "[aout]"
    elif voice_index is not None:
        chains.append(f"[{voice_index}:a]anull[aout]")
        audio_out = "[aout]"
    elif music_index is not None:
        chains.append(f"[{music_index}:a]anull[aout]")
        audio_out = "[aout]"

    args += ["-filter_complex", ";".join(chains), "-map", "[vout]"]
    if audio_out:
        args += ["-map", audio_out, "-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        args += ["-an"]

    args += [
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(dst),
    ]

    code, _, stderr = await _run(*args)
    if code != 0:
        raise EditError(f"ffmpeg упал на сборке: {stderr.decode(errors='replace')[-600:]}")
    if not dst.exists() or dst.stat().st_size == 0:
        raise EditError("ffmpeg отработал без ошибки, но файл пустой.")
    return dst


async def probe_duration(path: str | Path) -> float:
    """Seconds. Used to time subtitles against a rendered voiceover."""
    code, stdout, stderr = await _run(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    )
    if code != 0:
        raise EditError(f"ffprobe не смог прочитать {path}: {stderr.decode(errors='replace')[:200]}")
    try:
        return float(stdout.decode().strip())
    except ValueError as e:
        raise EditError(f"Не разобрал длительность {path}") from e


def subtitles_from_script(text: str, total_seconds: float, max_chars: int = 42) -> list[Subtitle]:
    """
    Splits a voiceover script into timed captions, weighting each line by its
    length so long lines hold longer. Good enough for burned-in reel subtitles;
    real word-level timing would need forced alignment.
    """
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    if not lines:
        return []

    total_chars = sum(len(line) for line in lines) or 1
    subtitles, cursor = [], 0.0
    for line in lines:
        span = total_seconds * (len(line) / total_chars)
        subtitles.append(Subtitle(start=cursor, end=cursor + span, text=line))
        cursor += span
    return subtitles
