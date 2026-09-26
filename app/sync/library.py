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


def run_library_rescan(session_factory, job_id: int, remove: bool = True) -> None:
    """Verify every completed track's file; damaged ones are removed (or
    flagged when remove=False) and reset to pending for re-download."""
    import logging
    import os
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.audio.probe import check_playable
    from app.db.models import SyncJob, Track
    from app.sync.prune import prune_empty_parents
    from app.config import get_settings
    log = logging.getLogger("yakcheesemusic")
    music_dir = get_settings().music_dir
    db = session_factory()
    try:
        job = db.get(SyncJob, job_id)
        rows = db.execute(select(Track).where(Track.status == "completed")).scalars().all()
        targets = [(t.id, t.local_path) for t in rows if t.local_path]
        log.info("job=%s action=rescan status=start files=%s remove=%s",
                 job_id, len(targets), remove)
        if job:
            job.tracks_seen = len(targets)
            db.commit()
        damaged, errors = [], []
        for tid, path in targets:
            reason = check_playable(path or "")
            if reason is None:
                continue
            t = db.get(Track, tid)
            if remove and path and os.path.exists(path):
                try:
                    os.remove(path)
                    try:
                        prune_empty_parents(path, music_dir)
                    except Exception:
                        pass
                except OSError as e:
                    errors.append(f"{path}: {e}")
                    continue
            if t and remove:
                t.local_path = None
                t.status = "pending"
            damaged.append(f"{path} ({reason})")
            if remove:
                db.commit()
        if job:
            job.tracks_downloaded = len(damaged)
            job.tracks_failed = len(errors)
            if damaged or errors:
                job.error = "; ".join((damaged + errors)[:10])
            job.status = "success" if not errors else "failed"
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        log.info("job=%s action=rescan status=done damaged=%s errors=%s",
                 job_id, len(damaged), len(errors))
    except Exception as e:
        log.exception("job=%s action=rescan status=error", job_id)
        try:
            job = db.get(SyncJob, job_id)
            if job:
                from datetime import datetime, timezone
                job.status = "failed"
                job.error = str(e)[:1000]
                job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
