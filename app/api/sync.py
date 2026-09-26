from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db.models import SyncJob

router = APIRouter(tags=["sync"])


@router.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    rows = db.execute(select(SyncJob).order_by(desc(SyncJob.id)).limit(50)).scalars().all()
    return [{"id": j.id, "playlist_id": j.playlist_id, "status": j.status,
             "started_at": j.started_at, "finished_at": j.finished_at} for j in rows]


@router.get("/api/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    j = db.get(SyncJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": j.id, "status": j.status, "error": j.error,
            "tracks_seen": j.tracks_seen, "tracks_downloaded": j.tracks_downloaded}


@router.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    return build_stats(db)


@router.post("/api/replaygain")
def replaygain_sweep(db: Session = Depends(get_db)):
    """Tag the whole library at the configured LUFS target (background job)."""
    import threading
    from app.config import get_settings
    from app.db.database import SessionLocal
    from app.audio.replaygain import run_library_sweep
    running = db.execute(select(SyncJob).where(
        SyncJob.playlist_id.is_(None), SyncJob.status == "running",
        SyncJob.finished_at.is_(None))).scalars().first()
    if running:
        from fastapi import HTTPException
        raise HTTPException(status_code=409,
                            detail=f"ReplayGain sweep already running (job {running.id})")
    s = get_settings()
    job = SyncJob(playlist_id=None, status="running")
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(target=run_library_sweep,
                     args=(SessionLocal, job.id, s.music_dir,
                           s.replaygain_target_lufs), daemon=True).start()
    return {"ok": True, "job_id": job.id, "target_lufs": s.replaygain_target_lufs}


@router.post("/api/library/convert")
def library_convert(db: Session = Depends(get_db)):
    """Convert all completed lossless tracks to 320k MP3 (background job)."""
    import threading
    from app.db.database import SessionLocal
    from app.audio.convert import run_library_convert
    running = db.execute(select(SyncJob).where(
        SyncJob.playlist_id.is_(None), SyncJob.status == "running",
        SyncJob.finished_at.is_(None))).scalars().first()
    if running:
        from fastapi import HTTPException
        raise HTTPException(status_code=409,
                            detail=f"Library job already running (job {running.id})")
    job = SyncJob(playlist_id=None, status="running")
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(target=run_library_convert,
                     args=(SessionLocal, job.id), daemon=True).start()
    return {"ok": True, "job_id": job.id}


@router.post("/api/library/retag")
def library_retag(db: Session = Depends(get_db)):
    """Backfill playlist-derived tags onto all completed files (background job)."""
    import threading
    from app.db.database import SessionLocal
    from app.metadata.tagging import run_library_retag
    running = db.execute(select(SyncJob).where(
        SyncJob.playlist_id.is_(None), SyncJob.status == "running",
        SyncJob.finished_at.is_(None))).scalars().first()
    if running:
        from fastapi import HTTPException
        raise HTTPException(status_code=409,
                            detail=f"Library job already running (job {running.id})")
    job = SyncJob(playlist_id=None, status="running")
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(target=run_library_retag,
                     args=(SessionLocal, job.id), daemon=True).start()
    return {"ok": True, "job_id": job.id}


@router.post("/api/library/rescan")
def library_rescan(payload: dict | None = None, db: Session = Depends(get_db)):
    """Verify completed files; remove unplayable ones and queue re-download.
    Pass {"remove": false} for a report-only dry run."""
    import threading
    from app.db.database import SessionLocal
    from app.sync.library import run_library_rescan
    running = db.execute(select(SyncJob).where(
        SyncJob.playlist_id.is_(None), SyncJob.status == "running",
        SyncJob.finished_at.is_(None))).scalars().first()
    if running:
        from fastapi import HTTPException
        raise HTTPException(status_code=409,
                            detail=f"Library job already running (job {running.id})")
    remove = (payload or {}).get("remove", True)
    job = SyncJob(playlist_id=None, status="running")
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(target=run_library_rescan,
                     args=(SessionLocal, job.id, remove), daemon=True).start()
    return {"ok": True, "job_id": job.id, "remove": remove}


def build_stats(db: Session) -> dict:
    from sqlalchemy import func, desc
    from app.db.models import Playlist, PlaylistTrack, Track
    by_status = dict(db.execute(
        select(Track.status, func.count(Track.id)).group_by(Track.status)).all())

    def _tlist(status: str, limit: int = 20):
        rows = db.execute(select(Track).where(Track.status == status)
                          .order_by(desc(Track.updated_at)).limit(limit)).scalars().all()
        return [{"id": t.id, "title": t.title, "artist": t.artist,
                 "provider": t.download_provider} for t in rows]

    recent = db.execute(
        select(Track).where(Track.status.in_(["completed", "failed", "needs_review"]))
        .order_by(desc(Track.updated_at)).limit(10)).scalars().all()
    jobs = db.execute(select(SyncJob).order_by(desc(SyncJob.id)).limit(5)).scalars().all()
    playlists = db.execute(select(Playlist)).scalars().all()
    progress = []
    for p in playlists:
        rows = db.execute(select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == p.id,
            PlaylistTrack.active.is_(True))).scalars().all()
        done = sum(1 for m in rows if m.track and m.track.status == "completed")
        progress.append({"id": p.id, "name": p.name or p.url, "provider": p.provider,
                         "total": len(rows), "done": done,
                         "last_sync_at": p.last_sync_at})
    last_job = jobs[0] if jobs else None
    return {
        "playlists": len(playlists),
        "tracks": db.execute(select(func.count(Track.id))).scalar() or 0,
        "by_status": by_status,
        "downloading": _tlist("downloading"),
        "searching": _tlist("searching"),
        "recent": [{"id": t.id, "title": t.title, "artist": t.artist,
                    "status": t.status, "format": t.format,
                    "quality": t.quality_label} for t in recent],
        "jobs": [{"id": j.id, "playlist_id": j.playlist_id, "status": j.status,
                  "seen": j.tracks_seen, "downloaded": j.tracks_downloaded,
                  "failed": j.tracks_failed, "added": j.tracks_added,
                  "removed": j.tracks_removed,
                  "started_at": j.started_at, "finished_at": j.finished_at}
                 for j in jobs],
        "progress": progress,
        "last_job": {"id": last_job.id, "status": last_job.status,
                     "started_at": last_job.started_at,
                     "finished_at": last_job.finished_at} if last_job else None,
    }
