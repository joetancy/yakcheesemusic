"""Post-download audio probing with mutagen (measured, not guessed)."""

from __future__ import annotations

import os


def check_playable(path: str) -> str | None:
    """None if the file looks playable, else a short reason.

    Catches: missing files, 0-byte corpses, unparseable garbage, and
    tag-only files with no audio stream. Truncated-but-parseable files
    can slip through (that needs a full ffmpeg decode).
    """
    if not path or not os.path.exists(path):
        return "missing file"
    try:
        if os.path.getsize(path) == 0:
            return "empty file"
    except OSError as e:
        return f"unreadable: {e}"
    try:
        from mutagen import File as MFile

        audio = MFile(path)
    except Exception as e:
        return f"unparseable: {type(e).__name__}"
    if audio is None:
        return "unknown format"
    info = getattr(audio, "info", None)
    length = getattr(info, "length", None) if info else None
    if not length or length <= 0:
        return "no audio stream"
    return None


def probe_audio(path: str) -> dict:
    """Return {bitrate_kbps, sample_rate, bit_depth} from the actual file."""
    from mutagen import File as MFile

    out: dict = {}
    try:
        audio = MFile(path)
    except Exception:
        return out
    info = getattr(audio, "info", None)
    if not info:
        return out
    br = getattr(info, "bitrate", None)  # mp3 etc: bits/sec
    if br:
        out["bitrate_kbps"] = int(round(br / 1000))
    sr = getattr(info, "sample_rate", None)
    if sr:
        out["sample_rate"] = int(sr)
    bd = getattr(info, "bits_per_sample", None)
    if bd:
        out["bit_depth"] = int(bd)
    return out


def below_bitrate_floor(path: str, floor_kbps: int = 320) -> tuple[bool | None, str]:
    """Classify a file against a bitrate floor.

    Returns (True, description) for files below the floor, (False, description)
    for lossless or sufficiently high-bitrate audio, and (None, description)
    when the codec or bitrate cannot be established safely.
    """
    from pathlib import Path
    from mutagen import File as MFile

    try:
        audio = MFile(path)
    except Exception as exc:
        return None, f"unreadable ({type(exc).__name__})"
    if audio is None or not getattr(audio, "info", None):
        return None, "audio information unavailable"

    info = audio.info
    suffix = Path(path).suffix.lower()
    codec = str(
        getattr(info, "codec", "") or getattr(info, "codec_description", "") or ""
    ).lower()
    info_type = type(info).__name__.lower()
    lossless_exts = {".flac", ".alac", ".wav", ".wv", ".ape", ".aif", ".aiff"}
    lossless_codecs = ("flac", "alac", "wavpack", "ape", "pcm")
    if (
        suffix in lossless_exts
        or any(name in codec for name in lossless_codecs)
        or any(name in info_type for name in ("flac", "wavpack", "ape", "aiff", "wave"))
    ):
        label = codec.upper() if codec else suffix.lstrip(".").upper()
        return False, f"{label} lossless"

    bitrate = getattr(info, "bitrate", None)
    if not bitrate:
        return None, f"{codec or suffix.lstrip('.').upper() or 'unknown'} bitrate unavailable"
    bitrate_kbps = int(round(bitrate / 1000))
    label = codec.upper() if codec else suffix.lstrip(".").upper()
    description = f"{label or 'audio'} {bitrate_kbps} kbps"
    return bitrate_kbps < floor_kbps, description
