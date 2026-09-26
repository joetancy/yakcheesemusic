"""Audio quality comparison (Phase 7/12)."""
from __future__ import annotations

FORMAT_RANK = {
    "flac24": 60, "flac16": 50, "flac": 45, "alac": 40,
    "aac": 30, "mp3": 20, "ogg": 15, "m4a": 25,
}

LOSSLESS = {"flac", "flac24", "flac16", "alac", "wav"}


def quality_key(fmt: str | None, bit_depth: int | None, sample_rate: int | None, bitrate: int | None) -> tuple:
    f = (fmt or "").lower()
    lossless = 1 if f in LOSSLESS or f.startswith("flac") else 0
    return (lossless, FORMAT_RANK.get(f, 0), bit_depth or 0, sample_rate or 0, bitrate or 0)


def is_upgrade(current: dict, candidate: dict) -> bool:
    return quality_key(candidate.get("format"), candidate.get("bit_depth"),
                       candidate.get("sample_rate"), candidate.get("bitrate")) > quality_key(
        current.get("format"), current.get("bit_depth"), current.get("sample_rate"), current.get("bitrate"))
