"""ReplayGain via rsgain: track gain at target LUFS, tags only, never re-encode.

rsgain custom -s i -l <target> -S : scan + write tags, skip files that
already have ReplayGain info (re-runs are cheap). Album gain is deferred
until album grouping is reliable (plan §13).
"""
from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("music-sync")

SUPPORTED_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga",
                  ".opus", ".wav", ".aif", ".aiff", ".wv"}


def scan_library(music_dir: str) -> list[str]:
    """All rsgain-supported audio files under music_dir, sorted."""
    out = []
    for root, dirs, files in os.walk(music_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if os.path.splitext(fn)[1].lower() in SUPPORTED_EXTS:
                out.append(os.path.join(root, fn))
    return sorted(out)


def build_command(paths: list[str], target_lufs: float) -> list[str]:
    return ["rsgain", "custom", "-s", "i", "-l", str(target_lufs),
            "-S", "-q", *paths]


def apply_replaygain(paths: list[str], target_lufs: float = -14.0,
                     chunk: int = 200, timeout_s: int = 1200) -> tuple[int, list[str]]:
    """Tag files; return (ok_count, failed_paths). Raises on rsgain missing."""
    ok, failed = 0, []
    for i in range(0, len(paths), chunk):
        batch = paths[i:i + chunk]
        try:
            subprocess.run(build_command(batch, target_lufs), check=True,
                           capture_output=True, text=True, timeout=timeout_s)
            ok += len(batch)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            log.warning("rsgain batch %s failed: %s", i // chunk, e)
            failed.extend(batch)
    return ok, failed


def apply_one(path: str, target_lufs: float = -14.0) -> bool:
    ok, _ = apply_replaygain([path], target_lufs)
    return ok == 1


def run_library_sweep(session_factory, job_id: int, music_dir: str,
                      target_lufs: float) -> None:
    """Background full-library sweep, progress recorded on the SyncJob."""
    from datetime import datetime, timezone
    from app.db.models import SyncJob
    db = session_factory()
    try:
        job = db.get(SyncJob, job_id)
        files = scan_library(music_dir)
        log.info("job=%s action=replaygain status=start files=%s target=%s",
                 job_id, len(files), target_lufs)
        if job:
            job.tracks_seen = len(files)
            db.commit()
        ok, failed = apply_replaygain(files, target_lufs)
        if job:
            job.tracks_downloaded = ok
            job.tracks_failed = len(failed)
            if failed:
                job.error = "; ".join(failed[:10])
            job.status = "success" if not failed else "failed"
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        log.info("job=%s action=replaygain status=done ok=%s failed=%s",
                 job_id, ok, len(failed))
    except Exception as e:
        log.exception("job=%s action=replaygain status=error", job_id)
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
