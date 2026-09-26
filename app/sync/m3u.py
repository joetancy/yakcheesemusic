"""Physical .m3u playlist files for players, generated from DB state.

Matches the existing library convention:
  <music>/_Playlists/<Name>.m3u with #EXTINF entries and paths relative
  to the _Playlists dir (e.g. ../Artist/Album/01 - Title.flac).
Only downloaded files (local_path exists on disk) are included, in
playlist order. Old Yubal files are never touched (different filenames).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Playlist, PlaylistTrack
from app.sync.library import sanitize

log = logging.getLogger("music-sync")

HEADER = "#EXTM3U"


def m3u_path(playlist_dir: str, playlist: Playlist) -> Path:
    base = sanitize(playlist.name or f"playlist-{playlist.id}", f"playlist-{playlist.id}")
    plain = Path(playlist_dir) / f"{base}.m3u"
    if plain.exists():
        return Path(playlist_dir) / f"{base} [{playlist.id}].m3u"
    return plain


def _display(artist: str, title: str) -> str:
    return " - ".join(p for p in [artist.strip(), title.strip()] if p).replace("\n", " ")


def generate_m3u(db: Session, playlist_id: int, music_dir: str,
                 playlist_dir: str) -> dict:
    playlist = db.get(Playlist, playlist_id)
    if not playlist:
        raise ValueError(f"Playlist {playlist_id} not found")
    Path(playlist_dir).mkdir(parents=True, exist_ok=True)
    members = db.execute(
        select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.active.is_(True))
        .order_by(PlaylistTrack.position)).scalars().all()
    lines = [HEADER, f"#PLAYLIST:{playlist.name}"]
    included = 0
    missing = 0
    for m in members:
        t = m.track
        if not t or not t.local_path or not os.path.exists(t.local_path):
            missing += 1
            continue
        artist = t.artist or m.source_artist
        title = t.title or m.source_title
        secs = int(t.duration_ms // 1000) if t.duration_ms else -1
        rel = os.path.relpath(t.local_path, playlist_dir)
        lines.append(f"#EXTINF:{secs},{_display(artist, title)}")
        lines.append(rel)
        included += 1
    dest = m3u_path(playlist_dir, playlist)
    tmp = dest.with_suffix(".m3u.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, dest)
    log.info("playlist=%s m3u=%s included=%s missing=%s",
             playlist.name, dest, included, missing)
    return {"path": str(dest), "included": included, "missing": missing}
