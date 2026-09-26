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
