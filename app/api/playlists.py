from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

import os

from app.db.database import get_db
from app.db.models import Playlist

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


@router.get("")
def list_playlists(db: Session = Depends(get_db)):
    rows = db.execute(select(Playlist)).scalars().all()
    return [{"id": p.id, "name": p.name, "provider": p.provider, "url": p.url,
             "enabled": p.enabled, "sync_enabled": p.sync_enabled,
             "sync_interval": p.sync_interval} for p in rows]


@router.post("")
def create_playlist(payload: dict, db: Session = Depends(get_db)):
    from app.providers.spotify import SpotifyProvider, extract_playlist_id

    url = payload.get("url", "")
    if not SpotifyProvider().validate_url(url):
        from fastapi import HTTPException
        raise HTTPException(status_code=400,
                            detail="Only Spotify playlist URLs are supported")
    # store the canonical URL: share links with ?si=/pi=/pt= params get
    # login-walled, which breaks anonymous reads (web harvest, embed)
    pid = extract_playlist_id(url)
    url = f"https://open.spotify.com/playlist/{pid}"
    p = Playlist(name=payload.get("name", url), provider="spotify", url=url)
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "provider": "spotify"}


@router.get("/{playlist_id}")
def get_playlist(playlist_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    p = db.get(Playlist, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": p.id, "name": p.name, "provider": p.provider, "url": p.url}


@router.patch("/{playlist_id}")
def update_playlist(playlist_id: int, payload: dict, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    p = db.get(Playlist, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    for k in ("name", "enabled", "sync_enabled", "sync_interval"):
        if k in payload:
            setattr(p, k, payload[k])
    db.commit()
    return {"ok": True}


@router.delete("/{playlist_id}")
def delete_playlist(playlist_id: int, delete_files: bool = False,
                    db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from sqlalchemy import select, func
    from app.config import get_settings
    from app.db.models import PlaylistTrack, Track
    from app.sync.prune import delete_track_file
    p = db.get(Playlist, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    deleted_files: list[str] = []
    kept_files = 0
    memberships = list(p.memberships)
    for m in memberships:
        t = db.get(Track, m.track_id) if m.track_id else None
        if not t:
            continue
        if delete_files and t.local_path:
            music_dir = get_settings().music_dir
            if delete_track_file(db, t, playlist_id, music_dir):
                deleted_files.append(t.local_path)
            else:
                kept_files += 1
    track_ids = [m.track_id for m in memberships if m.track_id]
    db.delete(p)
    db.flush()
    for tid in set(track_ids):
        refs = db.execute(select(func.count(PlaylistTrack.id)).where(
            PlaylistTrack.track_id == tid)).scalar() or 0
        if refs == 0:
            t = db.get(Track, tid)
            # drop the row when its file is gone (or it never downloaded);
            # keep rows for downloaded files as orphans
            if t and (not t.local_path or t.local_path in deleted_files):
                db.delete(t)
    db.commit()
    return {"ok": True, "deleted_files": len(deleted_files), "kept_files": kept_files}


@router.post("/{playlist_id}/scan")
def scan_playlist_route(playlist_id: int, db: Session = Depends(get_db)):
    import threading
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.db.database import SessionLocal
    from app.db.models import SyncJob
    from app.sync.scan import run_scan_job
    p = db.get(Playlist, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    running = db.execute(select(SyncJob).where(
        SyncJob.playlist_id == playlist_id, SyncJob.status == "running",
        SyncJob.finished_at.is_(None))).scalars().first()
    if running:
        raise HTTPException(status_code=409,
                            detail=f"Sync already running (job {running.id})")
    job = SyncJob(playlist_id=playlist_id, status="running")
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(target=run_scan_job,
                     args=(SessionLocal, playlist_id, job.id), daemon=True).start()
    return {"ok": True, "job_id": job.id}


@router.get("/{playlist_id}/tracks")
def playlist_tracks(playlist_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.db.models import PlaylistTrack
    p = db.get(Playlist, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    rows = db.execute(
        select(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position)).scalars().all()
    out = []
    for m in rows:
        t = m.track
        out.append({"position": m.position, "provider_track_id": m.provider_track_id,
                    "source_title": m.source_title, "source_artist": m.source_artist,
                    "source_album": m.source_album,
                    "active": m.active,
                    "track_id": t.id if t else None,
                    "status": t.status if t else None,
                    "album": (t.album or None) if t else None,
                    "format": t.format if t else None,
                    "quality": t.quality_label if t else None,
                    "local_path": t.local_path if t else None,
                    "download_provider": t.download_provider if t else None})
    return out


@router.post("/{playlist_id}/sync")
def sync_playlist_route(playlist_id: int, db: Session = Depends(get_db)):
    import threading
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.db.database import SessionLocal
    from app.db.models import SyncJob
    from app.sync.batch import run_playlist_sync
    p = db.get(Playlist, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    running = db.execute(select(SyncJob).where(
        SyncJob.playlist_id == playlist_id, SyncJob.status == "running",
        SyncJob.finished_at.is_(None))).scalars().first()
    if running:
        raise HTTPException(status_code=409,
                            detail=f"Sync already running (job {running.id})")
    job = SyncJob(playlist_id=playlist_id, status="running")
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(target=run_playlist_sync,
                     args=(SessionLocal, playlist_id, job.id), daemon=True).start()
    return {"ok": True, "job_id": job.id}


@router.post("/{playlist_id}/m3u")
def generate_playlist_file(playlist_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from app.config import get_settings
    from app.sync.m3u import generate_m3u
    p = db.get(Playlist, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        s = get_settings()
        plist_dir = s.playlist_dir or os.path.join(s.music_dir, "_Playlists")
        return generate_m3u(db, playlist_id, s.music_dir, plist_dir)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"m3u failed: {e}")
