"""Batch playlist sync: scan -> search missing -> auto-download confident matches.

Auto-gate: verdict "auto" always downloads; "auto_if_no_competition" downloads
unless a close rival (>=80, within 5 pts) exists; anything else -> needs_review.
Among near-tied candidates (within 3 pts of best) the better format wins.
Removed playlist entries are kept (policy: keep) — membership deactivated only.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Playlist, PlaylistTrack, SyncJob, Track

log = logging.getLogger("yakcheesemusic")

TODO_STATUSES = {"pending", "needs_review", "failed", "matched", "searching"}

FORMAT_RANK = {"flac": 50, "alac": 40, "wav": 45, "wv": 45, "ape": 45,
               "aac": 30, "m4a": 25, "mp3": 20, "ogg": 15, "opus": 15}


def quality_rank(fmt: str | None) -> int:
    return FORMAT_RANK.get((fmt or "").lower(), 0)


def pick_best(ranked: list[dict]) -> dict:
    """Best non-rejected candidate, preferring better formats on near-ties."""
    pool = [c for c in ranked if not c.get("rejected")] or ranked
    best = max(pool, key=lambda c: c["score"])
    contenders = [c for c in pool if best["score"] - c["score"] <= 3]
    return max(contenders, key=lambda c: (quality_rank(c.get("format")), c["score"]))


def decide(ranked: list[dict], auto: float = 90.0,
           conditional: float = 80.0, review: float = 65.0) -> tuple[str, dict | None]:
    """Return ("download", candidate) or ("review", None). Rejected are out."""
    pool = [c for c in ranked if not c.get("rejected")]
    if not pool:
        return "review", None
    best = max(pool, key=lambda c: c["score"])
    if best["verdict"] == "auto":
        return "download", pick_best(ranked)
    if best["verdict"] == "auto_if_no_competition":
        ordered = sorted(pool, key=lambda c: c["score"], reverse=True)
        runner = ordered[1] if len(ordered) > 1 else None
        if runner and runner["score"] >= conditional and \
                best["score"] - runner["score"] < 5:
            return "review", None
        return "download", pick_best(ranked)
    return "review", None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def search_one(session_factory, track_id: int) -> tuple[str, int, list[int]]:
    """Search+score+decide one track in its own session.

    Returns (outcome, track_id, ordered_candidate_ids) where outcome is
    "download", "review", or "failed".
    """
    from app.db.models import DownloadCandidate, Track
    from app.db.settings_store import get_thresholds
    from app.sync.search import rejected_keys
    from app.sync.search import search_candidates, save_candidates

    db = session_factory()
    try:
        t = db.get(Track, track_id)
        if not t:
            return "failed", track_id, []
        t.status = "searching"
        db.commit()
        try:
            auto, conditional, review = get_thresholds(db)
            ranked = search_candidates(t, auto, conditional, review)
        except Exception as e:
            log.warning("track=%s action=search status=error error=%s", track_id, e)
            t.status = "failed"
            db.commit()
            return "failed", track_id, []
        if not ranked:
            t.status = "needs_review"
            db.commit()
            return "review", track_id, []
        rej = rejected_keys(db, track_id)
        for c in ranked:
            c["rejected"] = c["provider_track_id"] in rej
        save_candidates(db, t.id, ranked)
        action, pick = decide(ranked, auto, conditional, review)
        if action == "review" or not pick:
            t.status = "needs_review"
            db.commit()
            return "review", track_id, []
        rows = db.execute(
            select(DownloadCandidate).where(DownloadCandidate.track_id == t.id)
            .order_by(DownloadCandidate.score.desc())).scalars().all()
        id_by_key = {c.provider_track_id: c.id for c in rows}
        pick_key = pick["provider_track_id"]
        if pick_key not in id_by_key:
            t.status = "needs_review"
            db.commit()
            return "review", track_id, []
        ordered = [id_by_key[pick_key]] + \
                  [c.id for c in rows
                   if c.id != id_by_key[pick_key] and not c.rejected_reason][:7]
        t.status = "downloading"
        t.download_provider = "soulseek"
        db.commit()
        return "download", track_id, ordered
    finally:
        db.close()


def run_playlist_sync(session_factory, playlist_id: int, job_id: int) -> None:
    """Full sync: parallel search phase, then parallel download phase."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.config import get_settings
    from app.sync.scan import scan_playlist
    from app.sync.download import download_track_worker

    s = get_settings()
    db = session_factory()
    try:
        job = db.get(SyncJob, job_id)
        playlist = db.get(Playlist, playlist_id)
        if not job or not playlist:
            db.close()
            return
        log.info("job=%s playlist=%s action=scan status=start", job_id, playlist.name)
        summary = scan_playlist(db, playlist_id)
        job.tracks_seen = summary["seen"]
        job.tracks_added = summary["added"]
        job.tracks_removed = summary["removed"]
        db.commit()

        members = db.execute(
            select(PlaylistTrack).where(
                PlaylistTrack.playlist_id == playlist_id,
                PlaylistTrack.active.is_(True))
            .order_by(PlaylistTrack.position)).scalars().all()
        todo = [m.track_id for m in members
                if m.track_id and db.get(Track, m.track_id).status not in
                ("downloading", "completed", "processing", "skipped")]
        db.close()

        to_download: list[tuple[int, list[int]]] = []
        with ThreadPoolExecutor(max_workers=s.sync_search_workers,
                                thread_name_prefix="search") as ex:
            futs = {ex.submit(search_one, session_factory, tid): tid for tid in todo}
            for f in as_completed(futs):
                outcome, tid, ordered = f.result()
                log.info("job=%s track=%s action=search status=%s", job_id, tid, outcome)
                if outcome == "download":
                    to_download.append((tid, ordered))

        with ThreadPoolExecutor(max_workers=s.sync_download_workers,
                                thread_name_prefix="dl") as ex:
            futs = {ex.submit(download_track_worker, tid, ordered): tid
                    for tid, ordered in to_download}
            for f in as_completed(futs):
                tid = futs[f]
                d = session_factory()
                try:
                    t = d.get(Track, tid)
                    j = d.get(SyncJob, job_id)
                    if t and t.status == "completed":
                        j.tracks_downloaded += 1
                        log.info("job=%s track=%s action=download status=success path=%s",
                                 job_id, tid, t.local_path)
                    else:
                        j.tracks_failed += 1
                        log.info("job=%s track=%s action=download status=failed", job_id, tid)
                    d.commit()
                finally:
                    d.close()

        db = session_factory()
        try:
            playlist = db.get(Playlist, playlist_id)
            job = db.get(SyncJob, job_id)
            playlist.last_sync_at = _now()
            try:  # Qobuz, then Deezer, for tracks Soulseek couldn't supply
                from app.sync.qobuz_fallback import (
                    run_deezer_fallback, run_qobuz_fallback)
                run_qobuz_fallback(session_factory, job_id, playlist_id)
                run_deezer_fallback(session_factory, job_id, playlist_id)
            except Exception as e:
                log.warning("job=%s streaming fallback failed: %s", job_id, e)
            job.status = "success"
            job.finished_at = _now()
            db.commit()
            try:  # refresh the player-facing .m3u (best effort)
                from app.sync.m3u import generate_m3u
                plist_dir = s.playlist_dir or os.path.join(s.music_dir, "_Playlists")
                generate_m3u(db, playlist_id, s.music_dir, plist_dir)
            except Exception as e:
                log.warning("job=%s m3u refresh failed: %s", job_id, e)
            log.info("job=%s action=sync status=success downloaded=%s failed=%s",
                     job_id, job.tracks_downloaded, job.tracks_failed)
        finally:
            db.close()
    except Exception:
        log.exception("job=%s action=sync status=error", job_id)
        try:
            db = session_factory()
            try:
                job = db.get(SyncJob, job_id)
                if job:
                    job.status = "failed"
                    job.error = "sync error (see logs)"
                    job.finished_at = _now()
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass
