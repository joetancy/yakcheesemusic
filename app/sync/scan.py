"""Playlist scan: read remote playlist, reconcile membership in SQLite (Phase 2/8)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Playlist, Track, PlaylistTrack
from app.providers.spotify import SpotifyProvider


def _provider_for(playlist: Playlist):
    if playlist.provider == "spotify":
        return SpotifyProvider()
    raise ValueError(f"Unsupported playlist provider: {playlist.provider}")


def scan_playlist(db: Session, playlist_id: int) -> dict:
    playlist = db.get(Playlist, playlist_id)
    if not playlist:
        raise ValueError(f"Playlist {playlist_id} not found")
    remote = _provider_for(playlist).get_playlist(playlist.url)

    if remote.name:
        playlist.name = remote.name

    seen_ids: set[str] = set()
    added = 0
    for remote_track in remote.tracks:
        seen_ids.add(remote_track.provider_track_id)
        existing = db.execute(
            select(PlaylistTrack).where(
                PlaylistTrack.playlist_id == playlist.id,
                PlaylistTrack.provider_track_id == remote_track.provider_track_id,
            )
        ).scalars().first()
        if existing:
            existing.position = remote_track.position
            existing.source_title = remote_track.title
            existing.source_artist = remote_track.artist
            existing.source_album = remote_track.album
            existing.source_duration_ms = remote_track.duration_ms
            existing.active = True
            existing.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            track = Track(
                title=remote_track.title, artist=remote_track.artist,
                album=remote_track.album, duration_ms=remote_track.duration_ms,
                isrc=remote_track.isrc, year=remote_track.year,
                disc_number=remote_track.disc_number,
                track_number=remote_track.track_number, status="pending",
            )
            db.add(track)
            db.flush()  # assign track.id
            db.add(PlaylistTrack(
                playlist_id=playlist.id, track_id=track.id,
                provider_track_id=remote_track.provider_track_id,
                position=remote_track.position,
                source_title=remote_track.title, source_artist=remote_track.artist,
                source_album=remote_track.album,
                source_duration_ms=remote_track.duration_ms, active=True,
            ))
            added += 1

    removed = db.execute(
        select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist.id, PlaylistTrack.active.is_(True))
    ).scalars().all()
    n_removed = 0
    for m in removed:
        if m.provider_track_id not in seen_ids:
            m.active = False
            n_removed += 1

    playlist.last_scan_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"seen": len(seen_ids), "added": added, "removed": n_removed,
            "name": playlist.name, "truncated": not remote.complete}
