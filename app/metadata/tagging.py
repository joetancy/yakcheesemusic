"""Write authoritative tags onto library files (mutagen).

Authority follows app.metadata.resolve: playlist-derived identity
(artist/title) wins; album/track_no/year come from the resolved metadata.
Junk values are never written. Existing frames outside our key set
(e.g. ReplayGain tags) are preserved.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("yakcheesemusic")

_VORBIS = {".flac", ".ogg", ".oga", ".opus"}
_MP4 = {".m4a", ".mp4", ".alac"}


def _clean(value) -> str | None:
    if value is None:
        return None
    from app.metadata.resolve import is_junk
    s = str(value).strip()
    if not s or is_junk(s):
        return None
    return s


def _track_no(value) -> str | None:
    try:
        n = int(str(value).split("/")[0].strip())
    except (ValueError, TypeError, AttributeError):
        return None
    return str(n) if 0 < n < 1000 else None


def _year_str(value) -> str | None:
    if value is None:
        return None
    import re
    m = re.search(r"(19|20)\d{2}", str(value))
    return m.group(0) if m else None


def _norm(tags: dict) -> dict:
    """Cleaned {title, artist, albumartist, album, tracknumber, date, genre, isrc}."""
    out = {
        "title": _clean(tags.get("title")),
        "artist": _clean(tags.get("artist")),
        "albumartist": _clean(tags.get("albumartist") or tags.get("artist")),
        "album": _clean(tags.get("album")),
        "tracknumber": _track_no(tags.get("tracknumber")),
        "date": _year_str(tags.get("date")),
        "genre": _clean(tags.get("genre")),
        "isrc": _clean(tags.get("isrc")),
    }
    return {k: v for k, v in out.items() if v}


def _set_id3_frames(audio, meta) -> None:
    from mutagen.id3 import TIT2, TPE1, TPE2, TALB, TRCK, TDRC, TCON, TSRC
    frames = {"title": TIT2, "artist": TPE1, "albumartist": TPE2,
              "album": TALB, "genre": TCON, "isrc": TSRC}
    enc = 3  # UTF-8
    for key, frame in frames.items():
        if meta.get(key) is not None:
            audio[frame.__name__] = frame(encoding=enc, text=meta[key])
    if meta.get("tracknumber") is not None:
        audio["TRCK"] = TRCK(encoding=enc, text=meta["tracknumber"])
    if meta.get("date") is not None:
        audio["TDRC"] = TDRC(encoding=enc, text=meta["date"])


def _write_id3(path: str, meta: dict) -> bool:
    from mutagen.id3 import ID3
    try:
        audio = ID3(path)
    except Exception:
        audio = ID3()  # fresh tag set (also covers raw/tagless mp3s)
    try:
        _set_id3_frames(audio, meta)
        audio.save(path)
    except Exception:
        return False
    return True


def _write_wave(path: str, meta: dict) -> bool:
    from mutagen import File as MFile
    try:
        audio = MFile(path)
        if audio is None or not hasattr(audio, "add_tags"):
            return False
        try:
            audio.add_tags()
        except Exception:
            pass
        if audio.tags is None:
            return False
        _set_id3_frames(audio.tags, meta)
        audio.save()
    except Exception:
        return False
    return True


def _write_vorbis(path: str, meta: dict) -> bool:
    from mutagen import File as MFile
    try:
        audio = MFile(path)
    except Exception:
        return False
    if audio is None or not hasattr(audio, "__setitem__"):
        return False
    keys = {"title": "TITLE", "artist": "ARTIST", "albumartist": "ALBUMARTIST",
            "album": "ALBUM", "tracknumber": "TRACKNUMBER",
            "date": "DATE", "genre": "GENRE", "isrc": "ISRC"}
    try:
        for key, vkey in keys.items():
            if meta.get(key) is not None:
                audio[vkey] = meta[key]
        audio.save()
    except Exception:
        return False
    return True


def _write_mp4(path: str, meta: dict) -> bool:
    from mutagen.mp4 import MP4
    try:
        audio = MP4(path)
    except Exception:
        return False
    keys = {"title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART",
            "album": "\xa9alb", "date": "\xa9day", "genre": "\xa9gen"}
    try:
        for key, mkey in keys.items():
            if meta.get(key) is not None:
                audio[mkey] = [meta[key]]
        if meta.get("tracknumber") is not None:
            audio["trkn"] = [(int(meta["tracknumber"]), 0)]
        audio.save()
    except Exception:
        return False
    return True


def write_tags(file_path: str, tags: dict) -> bool:
    """Stamp authoritative tags onto file_path. Never raises; False = skipped."""
    if not file_path or not os.path.exists(file_path):
        return False
    meta = _norm(tags or {})
    if not meta:
        return False
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".mp3":
            return _write_id3(file_path, meta)
        if ext in (".wav", ".wave", ".aiff", ".aif"):
            return _write_wave(file_path, meta)
        if ext in _MP4:
            return _write_mp4(file_path, meta)
        if ext in _VORBIS:
            return _write_vorbis(file_path, meta)
        # unknown extension: try generic mutagen mapping, else give up
        return _write_vorbis(file_path, meta)
    except Exception as e:
        log.warning("write_tags failed for %s: %s", file_path, e)
        return False


def run_library_retag(session_factory, job_id: int) -> None:
    """One-shot backfill: stamp every completed track's file from DB data."""
    import os
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.db.models import SyncJob, Track
    db = session_factory()
    try:
        job = db.get(SyncJob, job_id)
        rows = db.execute(select(Track).where(Track.status == "completed")).scalars().all()
        targets = [(t.id, t.local_path) for t in rows if t.local_path]
        log.info("job=%s action=retag status=start files=%s", job_id, len(targets))
        if job:
            job.tracks_seen = len(targets)
            db.commit()
        ok, failed = 0, []
        for tid, path in targets:
            t = db.get(Track, tid)
            if not t or not path or not os.path.exists(path):
                failed.append(path or str(tid))
                continue
            try:
                done = write_tags(path, {
                    "title": t.title, "artist": t.artist,
                    "albumartist": t.artist, "album": t.album,
                    "tracknumber": t.track_number, "date": t.year,
                    "isrc": t.isrc})
            except Exception as e:
                log.warning("job=%s retag error %s: %s", job_id, path, e)
                done = False
            if done:
                ok += 1
            else:
                failed.append(path)
        if job:
            job.tracks_downloaded = ok
            job.tracks_failed = len(failed)
            if failed:
                job.error = "; ".join(failed[:10])
            job.status = "success" if not failed else "failed"
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        log.info("job=%s action=retag status=done ok=%s failed=%s",
                 job_id, ok, len(failed))
    except Exception as e:
        log.exception("job=%s action=retag status=error", job_id)
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
