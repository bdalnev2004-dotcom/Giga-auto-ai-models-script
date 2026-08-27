"""
Video uniquification — the `uniquify` step, Drive folder 16_уникализировано.

Instagram fingerprints video perceptually, so reposting one reel across several
farm accounts (or re-uploading a good one later) can be flagged as a duplicate:
reach gets cut, or the upload is dropped. This re-encodes the file with small
geometric/colour/temporal changes so the fingerprint differs while the content
looks the same to a viewer.

Runs ffmpeg locally instead of routing through the third-party @unikalizator_videobot:
our bot cannot message another bot (Telegram's 2026-05 bot-to-bot mode needs a mutual
opt-in we don't control on their side), so that path would require a separate userbot
account with a real phone number.

NOT a guarantee — Instagram's duplicate detection survives light edits. This lowers
the odds, it does not remove them.

Parameters are derived from (account_id, variant): every account gets a consistently
different treatment, and the same inputs always reproduce the same output.
"""
import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path

FFMPEG = "ffmpeg"

FFPROBE = "ffprobe"


class UniquifyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Recipe:
    """The concrete edit applied to one file. Logged so a result stays explainable."""
    crop_pct: float      # fraction trimmed off each edge, then scaled back to size
    brightness: float    # eq: -1..1, we stay within ±0.03
    contrast: float      # eq: 1.0 is neutral
    saturation: float    # eq: 1.0 is neutral
    hue_deg: float       # hue rotation in degrees
    noise: int           # noise filter strength
    speed: float         # 1.0 is neutral; audio is pitch-corrected via atempo
    mirror: bool

    def describe(self) -> str:
        return (
            f"crop {self.crop_pct * 100:.1f}%, bright {self.brightness:+.3f}, "
            f"contrast {self.contrast:.3f}, sat {self.saturation:.3f}, "
            f"hue {self.hue_deg:+.1f}°, noise {self.noise}, speed {self.speed:.3f}"
            + (", mirrored" if self.mirror else "")
        )


def build_recipe(account_id: int, variant: int = 0, allow_mirror: bool = False) -> Recipe:
    """
    Deterministic per (account, variant). Ranges are deliberately small — the point is
    a different fingerprint, not a visibly different video.
    """
    rng = random.Random(f"giga-uniquify:{account_id}:{variant}")
    return Recipe(
        crop_pct=rng.uniform(0.010, 0.035),
        brightness=rng.uniform(-0.030, 0.030),
        contrast=rng.uniform(0.97, 1.04),
        saturation=rng.uniform(0.94, 1.07),
        hue_deg=rng.uniform(-4.0, 4.0),
        noise=rng.randint(3, 9),
        speed=rng.uniform(0.97, 1.03),
        # Off by default: mirroring reverses any burned-in subtitles or logo.
        mirror=allow_mirror and rng.random() < 0.5,
    )


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as e:
        raise UniquifyError(
            f"{args[0]} не найден. Установи ffmpeg (в Docker-образе он ставится через apt)."
        ) from e
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


async def _probe(src: Path) -> tuple[int, int, bool]:
    """Returns (width, height, has_audio)."""
    code, stdout, stderr = await _run(
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", str(src),
    )
    if code != 0:
        raise UniquifyError(f"ffprobe не смог прочитать {src.name}: {stderr.decode(errors='replace')[:300]}")
    streams = json.loads(stdout).get("streams", [])
    if not streams:
        raise UniquifyError(f"В {src.name} нет видеодорожки.")
    width, height = int(streams[0]["width"]), int(streams[0]["height"])

    code, stdout, _ = await _run(
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_streams", "-select_streams", "a:0", str(src),
    )
    has_audio = code == 0 and bool(json.loads(stdout).get("streams"))
    return width, height, has_audio


def _even(n: int) -> int:
    """H.264 needs even dimensions."""
    return n - (n % 2)


def _video_filter(recipe: Recipe, width: int, height: int) -> str:
    # Crop inward, then scale back to the original size: output keeps the 1080x1920
    # Reels geometry while the framing has genuinely shifted.
    dx, dy = int(width * recipe.crop_pct), int(height * recipe.crop_pct)
    cw, ch = _even(width - 2 * dx), _even(height - 2 * dy)

    chain = [
        f"crop={cw}:{ch}:{dx}:{dy}",
        f"scale={width}:{height}:flags=lanczos",
        f"eq=brightness={recipe.brightness:.4f}:contrast={recipe.contrast:.4f}"
        f":saturation={recipe.saturation:.4f}",
        f"hue=h={recipe.hue_deg:.2f}",
        f"noise=alls={recipe.noise}:allf=t",
    ]
    if recipe.mirror:
        chain.append("hflip")
    if recipe.speed != 1.0:
        chain.append(f"setpts={1 / recipe.speed:.6f}*PTS")
    return ",".join(chain)


async def uniquify(
    src: str | Path,
    dst: str | Path,
    account_id: int,
    variant: int = 0,
    allow_mirror: bool = False,
    crf: int = 20,
) -> Recipe:
    """
    Writes a re-encoded, fingerprint-shifted copy of `src` to `dst`.
    Returns the Recipe that was applied, so it can be logged against the ContentItem.

    allow_mirror: only pass True when the reel carries no text overlay or subtitles —
    a mirrored frame renders them backwards.
    """
    src, dst = Path(src), Path(dst)
    if not src.exists():
        raise UniquifyError(f"Исходный файл не найден: {src}")

    width, height, has_audio = await _probe(src)
    recipe = build_recipe(account_id, variant, allow_mirror)
    dst.parent.mkdir(parents=True, exist_ok=True)

    args = [
        FFMPEG, "-y", "-i", str(src),
        "-vf", _video_filter(recipe, width, height),
    ]
    if has_audio and recipe.speed != 1.0:
        # atempo keeps pitch intact; it accepts 0.5–2.0, and our range sits well inside.
        args += ["-af", f"atempo={recipe.speed:.6f}"]

    args += [
        "-map_metadata", "-1",           # drop camera/editor metadata — part of the fingerprint
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",       # Instagram uploads want the moov atom up front
        str(dst),
    ]

    code, _, stderr = await _run(*args)
    if code != 0:
        raise UniquifyError(f"ffmpeg упал: {stderr.decode(errors='replace')[-500:]}")
    if not dst.exists() or dst.stat().st_size == 0:
        raise UniquifyError("ffmpeg отработал без ошибки, но файл пустой.")

    return recipe


async def _main() -> int:
    """Standalone check: python -m services.uniquify_service in.mp4 out.mp4 --account 3"""
    import argparse
    import sys

    # Windows consoles default to a legacy codepage with no box-drawing glyphs;
    # printing one raises UnicodeEncodeError before any work happens.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


    parser = argparse.ArgumentParser(description="Уникализация видео через ffmpeg")
    parser.add_argument("src")
    parser.add_argument("dst")
    parser.add_argument("--account", type=int, default=1)
    parser.add_argument("--variant", type=int, default=0)
    parser.add_argument("--mirror", action="store_true", help="разрешить зеркалирование (ломает текст в кадре)")
    args = parser.parse_args()

    try:
        recipe = await uniquify(args.src, args.dst, args.account, args.variant, args.mirror)
    except UniquifyError as e:
        print(f"Ошибка: {e}")
        return 1
    size = Path(args.dst).stat().st_size / 1_048_576
    print(f"Готово: {args.dst} ({size:.1f} МБ)\nПрименено: {recipe.describe()}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(_main()))
