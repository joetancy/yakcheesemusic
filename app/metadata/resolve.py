"""Decide final artist/album/title for a download.

Authority: catalogue (playlist-derived identity) for artist/title;
for album: embedded tags -> sharer's folder name -> playlist album.
Embedded junk ("Unknown Album", empties) is ignored, never trusted.
"""
from __future__ import annotations

import os
import re

JUNK = {"", "unknown", "unknown artist", "unknown album", "untitled",
        "track", "audio", "-", "?", "none", "n/a"}


def is_junk(value: str | None) -> bool:
    return (value or "").strip().lower() in JUNK


def extract_album_from_path(remote_path: str, artist: str) -> str | None:
    """Sharers usually share Artist/Album/files — the parent dir is the album."""
    parts = [p for p in re.split(r"[\\/]", remote_path or "") if p]
    if len(parts) < 3:  # need user/share-nesting; a bare parent is not an album
        return None
    parent = parts[-2].strip()
    if not parent or parent.lower() == (artist or "").strip().lower():
        return None
    parent = re.sub(r"^\d{4}\s*[-_.\s]+\s*", "", parent)  # "2026 - CLICK" -> "CLICK"
    parent = re.sub(r"\s+", " ", parent).strip(" -_.")
    return parent or None


def resolve_metadata(catalogue: dict, embedded: dict, remote_path: str) -> dict:
    artist = catalogue.get("artist") or embedded.get("artist") or "Unknown Artist"
    title = catalogue.get("title") or embedded.get("title") or "Unknown Title"
    album = None
    if not is_junk(embedded.get("album")):
        album = (embedded["album"] or "").strip()
    if not album:
        album = extract_album_from_path(remote_path or "", artist)
    if not album:
        album = catalogue.get("album") or None
    if is_junk(album):
        album = None
    track_no = embedded.get("track_number") or catalogue.get("track_number")
    year = embedded.get("year") or catalogue.get("year")
    return {"artist": artist.strip(), "title": title.strip(),
            "album": (album or "Unknown Album").strip(),
            "track_number": track_no, "year": year}
