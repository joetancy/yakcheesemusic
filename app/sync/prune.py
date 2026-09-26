"""File deletion for playlist removal (opt-in via ?delete_files=true).

A file is removed only when no other ACTIVE playlist membership references
its track and no other track row points at the same path. Empty album/artist
dirs under the music dir are pruned afterwards.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlaylistTrack, Track

log = logging.getLogger("yakcheesemusic")


def track_in_use_elsewhere(db: Session, track_id: int, exclude_playlist_id: int) -> bool:
    return db.execute(select(PlaylistTrack.id).where(
        PlaylistTrack.track_id == track_id,
        PlaylistTrack.playlist_id != exclude_playlist_id,
        PlaylistTrack.active.is_(True)).limit(1)).scalars().first() is not None


def path_shared(db: Session, local_path: str, exclude_track_id: int) -> bool:
    return db.execute(select(Track.id).where(
        Track.local_path == local_path,
        Track.id != exclude_track_id).limit(1)).scalars().first() is not None


def prune_empty_parents(path: str, music_dir: str) -> None:
    root = os.path.realpath(music_dir)
    d = os.path.realpath(os.path.dirname(path))
    while d.startswith(root + os.sep) and d != root:
        try:
            os.rmdir(d)  # only succeeds when empty
        except OSError:
            break
        d = os.path.dirname(d)


def delete_track_file(db: Session, track: Track, playlist_id: int,
                      music_dir: str) -> str | None:
    """Delete track's file if unused elsewhere. Returns deleted path or None."""
    if not track.local_path:
        return None
    if track_in_use_elsewhere(db, track.id, playlist_id):
        return None
    if path_shared(db, track.local_path, track.id):
        return None
    try:
        if os.path.exists(track.local_path):
            os.remove(track.local_path)
            prune_empty_parents(track.local_path, music_dir)
            log.info("deleted file %s", track.local_path)
            return track.local_path
    except OSError as e:
        log.warning("could not delete %s: %s", track.local_path, e)
    return None
