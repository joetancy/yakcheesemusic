"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.config import get_settings
from app.db.database import init_db, get_db
from app.db.models import Playlist, Track, SyncJob
from app.api import playlists as playlists_api
from app.api import tracks as tracks_api
from app.api import sync as sync_api
from app.api import settings as settings_api
from app.sync.scheduler import start_scheduler, shutdown_scheduler

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("yakcheesemusic")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    Path(settings.music_dir).mkdir(parents=True, exist_ok=True)
    # recover from unclean shutdowns: jobs/threads died with the container
    from sqlalchemy import update
    from app.db.database import SessionLocal
    from app.db.models import SyncJob, Track
    db = SessionLocal()
    try:
        db.execute(update(SyncJob).where(SyncJob.status == "running",
                                         SyncJob.finished_at.is_(None))
                   .values(status="interrupted", finished_at=_now()))
        db.execute(update(Track).where(Track.status.in_(
            ["downloading", "searching", "processing"])).values(status="pending"))
        db.commit()
    finally:
        db.close()
    start_scheduler()
    log.info("yakcheesemusic started music_dir=%s", settings.music_dir)
    yield
    shutdown_scheduler()


app = FastAPI(title="yakcheesemusic", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    """LAN UI updates must apply immediately — never cache static assets."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response

app.include_router(playlists_api.router)
app.include_router(tracks_api.router)
app.include_router(sync_api.router)
app.include_router(settings_api.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    from app.api.sync import build_stats
    s = build_stats(db)
    playlists = db.execute(select(Playlist).limit(50)).scalars().all()
    return templates.TemplateResponse(request, "index.html", {
        "n_playlists": s["playlists"], "n_tracks": s["tracks"],
        "n_failed": s["by_status"].get("failed", 0),
        "n_review": s["by_status"].get("needs_review", 0),
        "by_status": s["by_status"], "downloading": s["downloading"],
        "searching": s["searching"], "recent": s["recent"],
        "jobs": s["jobs"], "progress": s["progress"],
        "last_job": s["last_job"], "playlists": playlists,
        "music_dir": settings.music_dir,
    })


@app.get("/playlists", response_class=HTMLResponse)
def playlists_page(request: Request, db: Session = Depends(get_db)):
    from app.db.models import PlaylistTrack
    playlists = db.execute(select(Playlist)).scalars().all()
    progress = {}
    for p in playlists:
        rows = db.execute(select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == p.id, PlaylistTrack.active.is_(True))).scalars().all()
        done = sum(1 for m in rows if m.track and m.track.status == "completed")
        from app.sync.library import playlist_usage, human_size
        size_bytes, _ = playlist_usage(db, p.id)
        progress[p.id] = {"total": len(rows), "done": done,
                          "size": human_size(size_bytes)}
    return templates.TemplateResponse(request, "playlists.html",
                                       {"playlists": playlists, "progress": progress})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    from app.db import settings_store
    return templates.TemplateResponse(request, "settings.html", {
        "match_auto": settings_store.get_setting(db, "match_auto"),
        "match_conditional": settings_store.get_setting(db, "match_conditional"),
        "match_review": settings_store.get_setting(db, "match_review"),
        "preferred_format": settings_store.get_setting(db, "preferred_format"),
        "music_dir": settings.music_dir,
        "replaygain_target": settings.replaygain_target_lufs,
        "removed_policy": settings.removed_track_policy,
        "tz": settings.tz,
    })


@app.get("/playlists/{playlist_id}", response_class=HTMLResponse)
def playlist_detail(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from app.db.models import PlaylistTrack
    p = db.get(Playlist, playlist_id)
    if not p:
        raise HTTPException(status_code=404, detail="Not found")
    memberships = db.execute(
        select(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position)).scalars().all()
    import os
    from app.sync.m3u import m3u_path
    from app.sync.library import playlist_usage, human_size
    plist_dir = settings.playlist_dir or os.path.join(settings.music_dir, "_Playlists")
    m3u = m3u_path(plist_dir, p)
    size_bytes, size_files = playlist_usage(db, playlist_id)
    return templates.TemplateResponse(request, "playlist_detail.html",
                                       {"playlist": p, "memberships": memberships,
                                        "m3u_path": str(m3u) if m3u.exists() else None,
                                        "total_size": human_size(size_bytes),
                                        "total_files": size_files})


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request, db: Session = Depends(get_db)):
    tracks = db.execute(select(Track).where(Track.status.in_(
        ["needs_review", "failed", "skipped"])).limit(200)).scalars().all()
    return templates.TemplateResponse(request, "review.html", {"tracks": tracks})


@app.get("/review/{track_id}", response_class=HTMLResponse)
def review_detail(track_id: int, request: Request, db: Session = Depends(get_db),
                 shown: int = 10):
    from fastapi import HTTPException
    from sqlalchemy import desc
    from app.db.models import DownloadCandidate
    from app.matching.scoring import classify_score
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    rows = db.execute(select(DownloadCandidate).where(
        DownloadCandidate.track_id == track_id).order_by(
        desc(DownloadCandidate.score))).scalars().all()
    from app.db.settings_store import get_thresholds
    auto, conditional, review = get_thresholds(db)
    cands = [{"id": c.id, "title": c.title, "artist": c.artist,
              "quality": c.quality, "score": c.score,
              "verdict": classify_score(c.score or 0, auto, conditional, review),
              "rejected": bool(c.rejected_reason), "source": c.source_url}
             for c in rows]
    # review page shows usable candidates in pages of 10 + all rejected
    # (so undo stays possible); ?shown=N pages through the usable ones
    shown = max(10, min(shown, 500))
    usable = [c for c in cands if not c["rejected"]]
    alive = usable[:shown]
    shown_list = alive + [c for c in cands if c["rejected"]]
    total = len(cands)
    if t.status == "failed":
        reason = "Last download attempt failed. Check candidates and retry, or search again."
    elif t.status == "skipped":
        reason = "Skipped by you — sync ignores this track until unskipped."
    elif not cands:
        reason = "No search results found. Try Search again later (peers change)."
    else:
        remaining = [c for c in cands if not c["rejected"]]
        if not remaining:
            reason = "All candidates were rejected. Unreject one or search again."
        else:
            b = remaining[0]
            reason = (f"Best usable match scores {b['score']:.1f} ({b['verdict']}) — "
                      f"below the auto-download line. Pick one manually or reject and re-search.")
    return templates.TemplateResponse(request, "review_detail.html",
                                       {"track": t, "candidates": shown_list,
                                        "reason": reason,
                                        "shown_usable": len(alive),
                                        "total_usable": len(usable),
                                        "total_candidates": total})
