from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import Track

router = APIRouter(prefix="/api/tracks", tags=["tracks"])


@router.get("")
def list_tracks(status: str | None = None, db: Session = Depends(get_db)):
    q = select(Track)
    if status:
        q = q.where(Track.status == status)
    rows = db.execute(q).scalars().all()
    return [{"id": t.id, "title": t.title, "artist": t.artist, "album": t.album,
             "status": t.status, "format": t.format} for t in rows]


@router.get("/{track_id}")
def get_track(track_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.db.models import DownloadCandidate
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    cands = db.execute(select(DownloadCandidate).where(
        DownloadCandidate.track_id == track_id).order_by(
        DownloadCandidate.score.desc())).scalars().all()
    return {"id": t.id, "title": t.title, "artist": t.artist, "album": t.album,
            "status": t.status, "format": t.format, "local_path": t.local_path,
            "download_provider": t.download_provider,
            "candidates": [{"id": c.id, "provider": c.provider, "title": c.title,
                            "artist": c.artist, "album": c.album,
                            "duration_ms": c.duration_ms, "quality": c.quality,
                            "format": c.format, "score": c.score,
                            "selected": c.selected,
                            "rejected": bool(c.rejected_reason),
                            "source": c.source_url} for c in cands]}


@router.post("/{track_id}/search")
def search_track(track_id: int, payload: dict | None = None,
                 db: Session = Depends(get_db)):
    import threading
    from fastapi import HTTPException
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    if t.status == "searching":
        raise HTTPException(status_code=409, detail="Search already running")
    custom_query = ((payload or {}).get("query") or "").strip() or None
    t.status = "searching"
    db.commit()
    threading.Thread(target=_run_search, args=(track_id, custom_query),
                     daemon=True).start()
    return {"ok": True, "status": "searching"}


@router.post("/{track_id}/search-internet-archive")
def search_internet_archive(track_id: int, db: Session = Depends(get_db)):
    """Manually replace current candidates with Archive.org matches."""
    import threading
    from fastapi import HTTPException
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    if t.status == "searching":
        raise HTTPException(status_code=409, detail="Search already running")
    t.status = "searching"
    db.commit()
    threading.Thread(target=_run_internet_archive_search, args=(track_id,),
                     daemon=True).start()
    return {"ok": True, "status": "searching"}


def _run_internet_archive_search(track_id: int, session_factory=None) -> None:
    """Background Archive search; replace candidates and never auto-download."""
    import logging
    from app.db.database import SessionLocal
    from app.db.models import DownloadCandidate
    from app.downloaders.internet_archive import InternetArchiveDownloader
    from app.matching.matcher import match_candidates

    log = logging.getLogger("yakcheesemusic")
    db = (session_factory or SessionLocal)()
    try:
        t = db.get(Track, track_id)
        if not t:
            return
        results = InternetArchiveDownloader().search(
            t.title, t.artist, t.album or "")
        from app.db.settings_store import get_thresholds
        auto, conditional, review = get_thresholds(db)
        source = {"title": t.title, "artist": t.artist, "album": t.album or "",
                  "duration_ms": t.duration_ms, "isrc": t.isrc}
        candidates = [{"provider": r.provider, "provider_track_id": r.provider_track_id,
                       "title": r.title, "artist": r.artist, "album": r.album,
                       "duration_ms": r.duration_ms, "quality": r.quality,
                       "format": r.format, "size": r.size,
                       "source_url": r.source_url} for r in results]
        ranked = match_candidates(source, candidates, auto, conditional, review)
        previously_rejected = {row.provider_track_id: row.rejected_reason
                               for row in db.query(DownloadCandidate).filter(
                                   DownloadCandidate.track_id == track_id,
                                   DownloadCandidate.rejected_reason.is_not(None)).all()}
        db.query(DownloadCandidate).filter(
            DownloadCandidate.track_id == track_id).delete(synchronize_session=False)
        for c in ranked:
            db.add(DownloadCandidate(
                track_id=track_id, provider=c["provider"],
                provider_track_id=c["provider_track_id"], title=c.get("title", ""),
                artist=c.get("artist", ""), album=c.get("album", ""),
                duration_ms=c.get("duration_ms"), quality=c.get("quality"),
                format=c.get("format"), size=c.get("size"),
                source_url=c.get("source_url"), score=c["score"],
                rejected_reason=previously_rejected.get(c["provider_track_id"])))
        t.status = "needs_review"
        db.commit()
        log.info("track=%s action=internet-archive-search status=done results=%s",
                 track_id, len(ranked))
    except Exception as e:
        log.warning("track=%s action=internet-archive-search status=error error=%s",
                    track_id, e)
        try:
            db.rollback()
            t = db.get(Track, track_id)
            if t:
                t.status = "needs_review"
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def _run_search(track_id: int, custom_query: str | None,
                session_factory=None) -> None:
    """Background body of a manual search (own session, thread-safe)."""
    import logging
    from app.db.database import SessionLocal
    from app.sync.search import search_candidates, save_candidates
    log = logging.getLogger("yakcheesemusic")
    db = (session_factory or SessionLocal)()
    try:
        t = db.get(Track, track_id)
        if not t:
            return
        try:
            from app.db.settings_store import get_thresholds
            ranked = search_candidates(t, *get_thresholds(db), query=custom_query)
        except Exception as e:
            log.warning("track=%s action=search status=error error=%s", track_id, e)
            t.status = "failed"
            db.commit()
            return
        if not ranked:
            t.status = "needs_review"
            db.commit()
            return
        save_candidates(db, track_id, ranked)  # also flags rejected on ranked
        pool = [c for c in ranked if not c.get("rejected")]
        best = max(pool, key=lambda c: c["score"]) if pool else None
        t.status = ("matched" if best and best["verdict"] in ("auto", "auto_if_no_competition")
                    else "needs_review")
        db.commit()
    except Exception:
        log.exception("track=%s action=search status=error", track_id)
        try:
            t = db.get(Track, track_id)
            if t:
                t.status = "failed"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/{track_id}/download")
def download_track(track_id: int, payload: dict | None = None, db: Session = Depends(get_db)):
    import threading
    from fastapi import HTTPException
    from sqlalchemy import select
    from app.db.models import DownloadCandidate
    from app.sync.download import download_track_worker
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    q = select(DownloadCandidate).where(DownloadCandidate.track_id == track_id)
    cid = (payload or {}).get("candidate_id")
    if cid:
        cand = db.get(DownloadCandidate, cid)
        if not cand or cand.track_id != track_id:
            raise HTTPException(status_code=404, detail="Candidate not found")
    else:
        cand = db.execute(q.where(DownloadCandidate.rejected_reason.is_(None))
                          .order_by(DownloadCandidate.score.desc())).scalars().first()
        if not cand:
            raise HTTPException(status_code=404, detail="No candidates — search first")
    cand.selected = True
    t.status = "downloading"
    t.download_provider = "soulseek"
    db.commit()
    # try the chosen candidate first, then the next-best as failover
    # (peers routinely go offline between search and download)
    others = db.execute(q.order_by(DownloadCandidate.score.desc()).limit(6)).scalars().all()
    ordered = [cand.id] + [c.id for c in others if c.id != cand.id]
    threading.Thread(target=download_track_worker,
                     args=(track_id, ordered), daemon=True).start()
    return {"ok": True, "status": "downloading", "candidate_id": cand.id}


RETRYABLE_STATUSES = ("needs_review", "failed", "skipped", "completed")


@router.post("/{track_id}/retry")
def retry_track(track_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    if t.status not in RETRYABLE_STATUSES:
        raise HTTPException(status_code=409,
                            detail=f"Track is {t.status}, nothing to retry")
    t.status = "pending"
    db.commit()
    return {"ok": True, "status": "pending"}


@router.post("/retry-all")
def retry_all(payload: dict | None = None, db: Session = Depends(get_db)):
    from sqlalchemy import update
    statuses = (payload or {}).get("statuses") or ["needs_review", "failed"]
    statuses = [s for s in statuses if s in RETRYABLE_STATUSES]
    if not statuses:
        return {"ok": True, "reset": 0}
    n = db.execute(update(Track).where(Track.status.in_(statuses))
                   .values(status="pending")).rowcount
    db.commit()
    return {"ok": True, "reset": n}


@router.post("/{track_id}/reject")
def reject_candidate(track_id: int, payload: dict, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from app.db.models import DownloadCandidate
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    cand = db.get(DownloadCandidate, payload.get("candidate_id"))
    if not cand or cand.track_id != track_id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cand.rejected_reason = "rejected by user"
    cand.selected = False
    db.commit()
    return {"ok": True}


@router.post("/{track_id}/unreject")
def unreject_candidate(track_id: int, payload: dict, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from app.db.models import DownloadCandidate
    cand = db.get(DownloadCandidate, (payload or {}).get("candidate_id"))
    if not cand or cand.track_id != track_id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cand.rejected_reason = None
    db.commit()
    return {"ok": True}


@router.post("/{track_id}/skip")
def skip_track(track_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    t.status = "skipped"
    db.commit()
    return {"ok": True}


@router.post("/{track_id}/unskip")
def unskip_track(track_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    t = db.get(Track, track_id)
    if not t:
        raise HTTPException(status_code=404, detail="Not found")
    t.status = "pending"
    db.commit()
    return {"ok": True}
