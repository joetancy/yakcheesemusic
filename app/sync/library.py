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


def make_readable(path: str) -> None:
    """Players (navidrome/jellyfin, uid 1000) must read root-written files."""
    import logging
    import os
    try:
        os.chmod(path, 0o644)
    except OSError as e:
        logging.getLogger("yakcheesemusic").warning(
            "chmod failed for %s: %s", path, e)


def replace_previous_file(old: str | None, new: str, music_dir: str) -> bool:
    """Remove the superseded file after a successful re-download.
    Returns True when something was removed."""
    import logging
    import os
    from app.sync.prune import prune_empty_parents
    if not old or old == new or not os.path.exists(old):
        return False
    try:
        os.remove(old)
    except OSError as e:
        logging.getLogger("yakcheesemusic").warning(
            "could not remove superseded %s: %s", old, e)
        return False
    try:
        prune_empty_parents(old, music_dir)
    except Exception:
        pass
    return True


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


AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav",
              ".wv", ".ape", ".alac", ".mp4", ".oga", ".aif", ".aiff"}


def scan_audio_files(music_dir: str) -> list[str]:
    """All audio files under music_dir, sorted. Skips dot-dirs and _Playlists."""
    import os
    out = []
    for root, dirs, files in os.walk(music_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "_Playlists"]
        for fn in files:
            if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                out.append(os.path.join(root, fn))
    return sorted(out)


def _md5(path: str) -> str | None:
    import hashlib
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def run_library_dedup(session_factory, job_id: int, remove: bool = True) -> None:
    """Delete audio files no track references (orphans); report byte-identical
    groups. Referenced files are never touched, even when identical."""
    import logging
    import os
    from collections import defaultdict
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.db.models import SyncJob, Track
    from app.sync.prune import prune_empty_parents
    from app.config import get_settings
    log = logging.getLogger("yakcheesemusic")
    music_dir = get_settings().music_dir
    db = session_factory()
    try:
        job = db.get(SyncJob, job_id)
        rows = db.execute(select(Track.local_path).where(
            Track.local_path.is_not(None))).scalars().all()
        referenced = {p for p in rows if p}
        files = scan_audio_files(music_dir)
        log.info("job=%s action=dedup status=start files=%s remove=%s",
                 job_id, len(files), remove)
        if job:
            job.tracks_seen = len(files)
            db.commit()
        orphans = [p for p in files if p not in referenced]
        removed, errors = [], []
        if remove:
            for p in orphans:
                try:
                    os.remove(p)
                    removed.append(p)
                    try:
                        prune_empty_parents(p, music_dir)
                    except Exception:
                        pass
                except OSError as e:
                    errors.append(f"{p}: {e}")
        by_size: dict[int, list[str]] = defaultdict(list)
        for p in files:
            if p in removed:
                continue
            try:
                by_size[os.path.getsize(p)].append(p)
            except OSError:
                pass
        identical = []
        for group in by_size.values():
            if len(group) < 2:
                continue
            by_hash: dict[str, list[str]] = defaultdict(list)
            for p in group:
                h = _md5(p)
                if h:
                    by_hash[h].append(p)
            identical.extend(sorted(g) for g in by_hash.values() if len(g) > 1)
        if job:
            job.tracks_downloaded = len(removed) if remove else len(orphans)
            job.tracks_failed = len(errors)
            notes = []
            if orphans:
                notes.append(f"orphans ({len(orphans)}): " + "; ".join(orphans[:10]))
            for g in identical[:5]:
                notes.append("identical: " + "; ".join(g))
            notes.extend(errors)
            if notes:
                job.error = "; ".join(notes)[:2000]
            job.status = "success" if not errors else "failed"
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        log.info("job=%s action=dedup status=done orphans=%s identical=%s errors=%s",
                 job_id, len(orphans), len(identical), len(errors))
    except Exception as e:
        log.exception("job=%s action=dedup status=error", job_id)
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
