"""Move completed tracks to their resolved Artist/Album location."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Track
from app.metadata.resolve import resolve_metadata
from app.metadata.tags import read_tags
from app.sync.library import library_path, place_file, sanitize

log = logging.getLogger("yakcheesemusic")


def resolved_location(track: Track, music_dir: str) -> Path:
    meta = resolve_metadata(
        {"title": track.title, "artist": track.artist, "album": track.album,
         "year": track.year, "track_number": track.track_number},
        read_tags(track.local_path) if track.local_path else {},
        track.download_source_url or "")
    return library_path(music_dir, meta["artist"], meta["album"], meta["title"],
                        Path(track.local_path).suffix if track.local_path else ".mp3",
                        meta["track_number"]), meta


def reorganize_track(db: Session, track_id: int) -> dict:
    """Move a completed track if it is not where it belongs. Returns outcome."""
    t = db.get(Track, track_id)
    if not t or t.status != "completed" or not t.local_path:
        return {"moved": False, "reason": "not completed"}
    if not os.path.exists(t.local_path):
        return {"moved": False, "reason": "file missing"}
    music_dir = get_settings().music_dir
    dest, meta = resolved_location(t, music_dir)
    if os.path.realpath(t.local_path) == os.path.realpath(dest):
        return {"moved": False, "reason": "already placed"}
    final = place_file(Path(t.local_path), dest)
    # prune newly-emptied old dirs (reuse prune logic bound to music dir)
    from app.sync.prune import prune_empty_parents
    prune_empty_parents(t.local_path, music_dir)
    t.local_path = str(final)
    if meta["album"] != "Unknown Album":
        t.album = meta["album"]
    if meta["track_number"]:
        t.track_number = meta["track_number"]
    if meta["year"]:
        t.year = meta["year"]
    # a better album/artist may change the stored quality dir only; keep tags
    db.commit()
    # also refresh the player .m3u files that pointed at the old path
    try:
        from app.sync.m3u import generate_m3u
        from app.db.models import PlaylistTrack
        from sqlalchemy import select
        plist_dir = get_settings().playlist_dir or os.path.join(music_dir, "_Playlists")
        pids = {m.playlist_id for m in db.execute(
            select(PlaylistTrack).where(PlaylistTrack.track_id == t.id)).scalars().all()}
        for pid in pids:
            generate_m3u(db, pid, music_dir, plist_dir)
    except Exception as e:
        log.warning("m3u refresh after move failed: %s", e)
    log.info("reorganized track=%s -> %s", track_id, final)
    return {"moved": True, "path": str(final), "album": meta["album"]}
