"""Lossless -> 320kbps MP3 transcode stage (ffmpeg, libmp3lame).

Metadata + embedded art ride along (-map 0). Source is removed only after
the MP3 verifies (probed duration > 0). ReplayGain must run AFTER this
stage, on the final file.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
import os

log = logging.getLogger("music-sync")

LOSSLESS = {"flac", "alac", "wav", "wv", "ape", "aiff", "aif"}


def should_convert(fmt: str | None, preferred: str) -> bool:
    return preferred == "mp3" and (fmt or "").lower() in LOSSLESS


def build_command(src: str, dest: str) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
            "-map", "0", "-c:a", "libmp3lame", "-b:a", "320k",
            "-c:v", "copy", "-id3v2_version", "3", dest]


def convert_to_mp3(src: str, timeout_s: int = 900) -> str:
    """Transcode src to 320k MP3 next to it; return mp3 path. Verifies output."""
    dest = os.path.splitext(src)[0] + ".mp3"
    if os.path.realpath(dest) == os.path.realpath(src):
        return dest
    fd, tmp = tempfile.mkstemp(suffix=".mp3", dir=os.path.dirname(src))
    os.close(fd)
    r = subprocess.run(build_command(src, tmp), capture_output=True, text=True,
                       timeout=timeout_s)
    if r.returncode != 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError(f"ffmpeg failed: {(r.stderr or '')[-300:]}")
    from app.audio.probe import probe_audio  # noqa
    try:
        from mutagen import File as MFile
        audio = MFile(tmp)
        dur = getattr(getattr(audio, "info", None), "length", 0) or 0
    except Exception:
        dur = 0
    if dur <= 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("ffmpeg produced an unplayable file")
    if os.path.exists(dest):
        base, i = dest, 2
        while os.path.exists(base):
            base = f"{os.path.splitext(dest)[0]} ({i}).mp3"
            i += 1
        dest = base
    os.replace(tmp, dest)
    os.remove(src)
    return dest


def run_library_convert(session_factory, job_id: int) -> None:
    """Convert all completed lossless tracks to MP3 320 in the background."""
    import os
    from datetime import datetime, timezone
    from app.config import get_settings
    from app.db.models import Playlist, Track
    from app.audio.probe import probe_audio
    from app.audio.replaygain import apply_one
    from sqlalchemy import select

    s = get_settings()
    db = session_factory()
    try:
        from app.db.models import SyncJob
        job = db.get(SyncJob, job_id)
        rows = db.execute(select(Track).where(Track.status == "completed")).scalars().all()
        targets = [t.id for t in rows
                   if t.local_path and (t.format or "").lower() in LOSSLESS
                   and os.path.exists(t.local_path)]
        log.info("job=%s action=convert status=start files=%s", job_id, len(targets))
        if job:
            job.tracks_seen = len(targets)
            db.commit()
        ok, failed = 0, []
        for tid in targets:
            t = db.get(Track, tid)
            try:
                new_path = convert_to_mp3(t.local_path)
                t.local_path = new_path
                t.format = "mp3"
                for k, v in probe_audio(new_path).items():
                    setattr(t, {"bitrate_kbps": "bitrate"}.get(k, k), v)
                apply_one(new_path, s.replaygain_target_lufs)
                db.commit()
                ok += 1
            except Exception as e:
                log.warning("job=%s convert failed %s: %s", job_id, t.local_path, e)
                failed.append(t.local_path or str(tid))
                db.rollback()
        if job:
            try:
                from app.sync.m3u import generate_m3u
                plist_dir = s.playlist_dir or os.path.join(s.music_dir, "_Playlists")
                for p in db.execute(select(Playlist)).scalars().all():
                    generate_m3u(db, p.id, s.music_dir, plist_dir)
            except Exception as e:
                log.warning("job=%s m3u refresh failed: %s", job_id, e)
            job.tracks_downloaded = ok
            job.tracks_failed = len(failed)
            if failed:
                job.error = "; ".join(failed[:10])
            job.status = "success" if not failed else "failed"
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        log.info("job=%s action=convert status=done ok=%s failed=%s", job_id, ok, len(failed))
    except Exception:
        log.exception("job=%s action=convert status=error", job_id)
    finally:
        db.close()
