"""Blocking single-track download with candidate failover (shared worker)."""
from __future__ import annotations

import logging

log = logging.getLogger("yakcheesemusic")


def download_track_worker(track_id: int, candidate_ids: list[int]) -> None:
    """Try candidates in order; first success wins. Own DB session (thread-safe)."""
    from pathlib import Path
    from app.db.database import SessionLocal
    from app.db.models import DownloadCandidate, Track
    from app.downloaders.base import SearchResult
    from app.downloaders.slskd import SlskdDownloader
    from app.sync.library import library_path, place_file
    from app.config import get_settings
    db = SessionLocal()
    try:
        t = db.get(Track, track_id)
        previous_path = t.local_path if t else None
        last_err: Exception | None = None
        for candidate_id in candidate_ids:
            cand = db.get(DownloadCandidate, candidate_id)
            if not cand:
                continue
            result = SearchResult(provider=cand.provider,
                                  provider_track_id=cand.provider_track_id,
                                  title=cand.title, artist=cand.artist, album=cand.album,
                                  duration_ms=cand.duration_ms, quality=cand.quality,
                                  format=cand.format, size=cand.size)
            try:
                tmp = Path(SlskdDownloader().download(result, "/downloads"))
            except Exception as e:
                log.warning("candidate %s failed (%s), trying next", candidate_id, e)
                last_err = e
                continue
            import os
            from app.sync.library import library_path as _lp, place_file as _place
            from app.sync.prune import prune_empty_parents
            from app.metadata.resolve import resolve_metadata
            from app.metadata.tags import read_tags
            music_dir = get_settings().music_dir
            first = _place(tmp, _lp(music_dir, t.artist or cand.artist,
                                    t.album or cand.album or "Unknown Album",
                                    t.title or cand.title,
                                    tmp.suffix or f".{cand.format or 'mp3'}",
                                    t.track_number))
            t.local_path = str(first)
            t.format = first.suffix.lstrip(".")
            t.download_source_url = f"soulseek://{result.provider_track_id}"
            # resolve the real album from the file's tags + sharer's folder,
            # then move into Artist/Album when the album is actually known
            try:
                meta = resolve_metadata(
                    {"title": t.title or cand.title, "artist": t.artist or cand.artist,
                     "album": t.album or cand.album, "year": t.year,
                     "track_number": t.track_number},
                    read_tags(str(first)), result.provider_track_id)
            except Exception as e:
                log.warning("resolve failed for %s: %s", first, e)
                meta = None
            final = first
            if meta and meta["album"] != "Unknown Album":
                better = _lp(music_dir, meta["artist"], meta["album"],
                             meta["title"], first.suffix, meta["track_number"])
                if os.path.realpath(better) != os.path.realpath(first):
                    final = _place(first, better)
                    prune_empty_parents(str(first), music_dir)
                    t.local_path = str(final)
                t.album = meta["album"]
                if meta["track_number"]:
                    t.track_number = meta["track_number"]
                if meta["year"]:
                    t.year = meta["year"]
            try:  # transcode stage: lossless -> 320k MP3 when preferred
                from app.audio.convert import should_convert, convert_to_mp3
                from app.db.settings_store import get_setting
                if should_convert(final.suffix.lstrip("."), get_setting(db, "preferred_format")):
                    final = Path(convert_to_mp3(str(final)))
                    t.local_path = str(final)
                    t.format = "mp3"
                    log.info("transcoded track=%s -> %s", track_id, final)
            except Exception as e:
                log.warning("transcode failed for %s: %s", final, e)
            try:  # stamp playlist-derived tags on the FINAL file (post-transcode)
                from app.metadata.tagging import write_tags
                if not write_tags(str(final), {
                        "title": t.title, "artist": t.artist,
                        "albumartist": t.artist, "album": t.album,
                        "tracknumber": t.track_number, "date": t.year,
                        "isrc": t.isrc}):
                    log.warning("retag skipped for %s", final)
            except Exception as e:
                log.warning("retag failed for %s: %s", final, e)
            from app.sync.library import make_readable as _readable
            _readable(str(final))
            try:  # probe the FINAL file (post-transcode)
                from app.audio.probe import probe_audio
                for k, v in probe_audio(str(final)).items():
                    setattr(t, {"bitrate_kbps": "bitrate"}.get(k, k), v)
            except Exception as e:
                log.warning("probe failed for %s: %s", final, e)
            try:  # track gain at target LUFS on the FINAL file, tags only
                from app.audio.replaygain import apply_one
                if not apply_one(str(final), get_settings().replaygain_target_lufs):
                    log.warning("replaygain failed for %s", final)
            except Exception as e:
                log.warning("replaygain error for %s: %s", final, e)
            from app.sync.library import replace_previous_file as _replace
            _replace(previous_path, str(final), music_dir)
            t.status = "completed"
            for c in db.query(DownloadCandidate).filter(
                    DownloadCandidate.track_id == track_id).all():
                c.selected = (c.id == candidate_id)
            db.commit()
            log.info("downloaded track=%s -> %s", track_id, final)
            return
        log.error("all candidates failed track=%s: %s", track_id, last_err)
        t = db.get(Track, track_id)
        if t:
            t.status = "failed"
            db.commit()
    except Exception:
        log.exception("download failed track=%s", track_id)
        t = db.get(Track, track_id)
        if t:
            t.status = "failed"
            db.commit()
    finally:
        db.close()
