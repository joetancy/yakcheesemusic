"""Library file layout: /music/Artist/Album/[NN - ]Title.ext (Phase 5/§14)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

_INVALID = re.compile(r'[\\/:*?"<>|]')


def sanitize(name: str, fallback: str = "Unknown") -> str:
    name = _INVALID.sub("_", (name or "").strip().rstrip("."))
    name = re.sub(r"\s+", " ", name).strip()
    return name or fallback


def library_path(music_dir: str, artist: str, album: str, title: str,
                 ext: str, track_number: int | None = None) -> Path:
    artist_d = sanitize(artist, "Unknown Artist")
    album_d = sanitize(album, "Unknown Album")
    base = sanitize(title, "Unknown Title")
    if track_number:
        base = f"{track_number:02d} - {base}"
    ext = ext if ext.startswith(".") else f".{ext}"
    return Path(music_dir) / artist_d / album_d / (base + ext)


def place_file(src: Path, dest: Path) -> Path:
    """Move download into library, renaming on collision. Never overwrite."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    final = dest
    i = 2
    while final.exists():
        final = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        i += 1
    shutil.move(str(src), str(final))
    return final


def human_size(n: int) -> str:
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def playlist_usage(db, playlist_id: int) -> tuple[int, int]:
    """(bytes_on_disk, files_with_local_path) for active memberships."""
    import os
    from sqlalchemy import select
    from app.db.models import PlaylistTrack
    total, files = 0, 0
    rows = db.execute(select(PlaylistTrack).where(
        PlaylistTrack.playlist_id == playlist_id,
        PlaylistTrack.active.is_(True))).scalars().all()
    for m in rows:
        t = m.track
        if t and t.local_path and os.path.exists(t.local_path):
            try:
                total += os.path.getsize(t.local_path)
                files += 1
            except OSError:
                pass
    return total, files
