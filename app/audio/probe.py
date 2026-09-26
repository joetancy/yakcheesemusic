"""Post-download audio probing with mutagen (measured, not guessed)."""
from __future__ import annotations


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
