"""Qobuz fallback: tracks Soulseek can't supply get a Qobuz attempt.

Eligible: status in (pending, needs_review, failed) with zero usable
(non-rejected) candidates of any provider — i.e. Soulseek came up empty
or everything was rejected. Tracks already holding usable candidates
(including prior Qobuz ones) are left alone, so this never loops.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select

from app.db.models import DownloadCandidate, SyncJob, Track

log = logging.getLogger("yakcheesemusic")

ELIGIBLE = ("pending", "needs_review", "failed")


def _usable_candidate_ids(db, track_id: int) -> list[int]:
    rows = db.execute(select(DownloadCandidate).where(
        DownloadCandidate.track_id == track_id,
        DownloadCandidate.rejected_reason.is_(None))
        .order_by(DownloadCandidate.score.desc())).scalars().all()
    return [c.id for c in rows]


def _attempt(session_factory, track_id: int) -> str:
    """One Qobuz search+decide; returns download|review|skip|failed."""
    from app.db.settings_store import get_thresholds
    from app.downloaders.streamrip_qobuz import QobuzDownloader
    from app.sync.batch import decide
    from app.sync.search import save_candidates
    db = session_factory()
    try:
        t = db.get(Track, track_id)
        if not t:
            return "skip"
        try:
            results = QobuzDownloader().search(t.title, t.artist, t.album or "")
        except Exception as e:
            log.warning("track=%s action=qobuz-search status=error error=%s", track_id, e)
            return "failed"
        if not results:
            return "skip"
        auto, conditional, review = get_thresholds(db)
        source = {"title": t.title, "artist": t.artist, "album": t.album or "",
                  "duration_ms": t.duration_ms, "isrc": t.isrc}
        dicts = [{"provider": r.provider, "provider_track_id": r.provider_track_id,
                  "title": r.title, "artist": r.artist, "album": r.album,
                  "duration_ms": r.duration_ms, "quality": r.quality,
                  "format": r.format, "size": r.size,
                  "source_url": r.source_url} for r in results]
        from app.matching.matcher import match_candidates
        ranked = match_candidates(source, dicts, auto, conditional, review)
        save_candidates(db, track_id, ranked)
        action, pick = decide(ranked, auto, conditional, review)
        if action == "review" or not pick:
            t.status = "needs_review"
            db.commit()
            return "review"
        rows = db.execute(select(DownloadCandidate).where(
            DownloadCandidate.track_id == track_id,
            DownloadCandidate.rejected_reason.is_(None))
            .order_by(DownloadCandidate.score.desc())).scalars().all()
        id_by_key = {c.provider_track_id: c.id for c in rows}
        if pick["provider_track_id"] not in id_by_key:
            t.status = "needs_review"
            db.commit()
            return "review"
        ordered = [id_by_key[pick["provider_track_id"]]]
        t.status = "downloading"
        t.download_provider = "qobuz"
        db.commit()
        return ("download", ordered)
    finally:
        db.close()


def run_qobuz_fallback(session_factory, job_id: int, playlist_id: int) -> dict:
    """Qobuz pass for soulseek-exhausted members of one playlist."""
    from app.config import get_settings
    from app.db.models import PlaylistTrack
    from app.downloaders.streamrip_qobuz import configured
    from app.sync.download import download_track_worker
    s = get_settings()
    summary = {"attempted": 0, "downloaded": 0, "review": 0}
    if not configured():
        return summary
    db = session_factory()
    try:
        members = db.execute(select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.active.is_(True))).scalars().all()
        todo = []
        for m in members:
            if not m.track_id:
                continue
            t = db.get(Track, m.track_id)
            if not t or t.status not in ELIGIBLE:
                continue
            if _usable_candidate_ids(db, m.track_id):
                continue
            todo.append(m.track_id)
    finally:
        db.close()
    todo = todo[:max(0, int(s.qobuz_max_per_run or 0))]
    if not todo:
        return summary
    log.info("job=%s action=qobuz-fallback status=start tracks=%s", job_id, len(todo))
    to_download: list[tuple[int, list[int]]] = []
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="qobuz") as ex:
        futs = {ex.submit(_attempt, session_factory, tid): tid for tid in todo}
        for f in as_completed(futs):
            tid = futs[f]
            try:
                outcome = f.result()
            except Exception as e:
                log.warning("track=%s action=qobuz status=error error=%s", tid, e)
                continue
            summary["attempted"] += 1
            if isinstance(outcome, tuple):
                _, ordered = outcome
                to_download.append((tid, ordered))
            elif outcome == "review":
                summary["review"] += 1
            log.info("job=%s track=%s action=qobuz status=%s", job_id, tid, outcome)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="qobuz-dl") as ex:
        futs = {ex.submit(download_track_worker, tid, ordered): tid
                for tid, ordered in to_download}
        for f in as_completed(futs):
            tid = futs[f]
            d = session_factory()
            try:
                t = d.get(Track, tid)
                j = d.get(SyncJob, job_id)
                if t and t.status == "completed":
                    summary["downloaded"] += 1
                    if j:
                        j.tracks_downloaded += 1
                elif j:
                    j.tracks_failed += 1
                d.commit()
            finally:
                d.close()
    log.info("job=%s action=qobuz-fallback status=done %s", job_id, summary)
    return summary
