"""Candidate search + scoring shared by the API and batch sync."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Track, DownloadCandidate
from app.downloaders.slskd import SlskdDownloader
from app.matching.matcher import match_candidates


def search_candidates(track: Track, auto: float = 90.0,
                      conditional: float = 80.0, review: float = 65.0) -> list[dict]:
    """Search download sources and return ranked candidate dicts (with score/verdict)."""
    results = SlskdDownloader().search(track.title, track.artist, track.album or "")
    source = {"title": track.title, "artist": track.artist,
              "album": track.album or "", "duration_ms": track.duration_ms,
              "isrc": track.isrc}
    dicts = [{"provider": r.provider, "provider_track_id": r.provider_track_id,
              "title": r.title, "artist": r.artist, "album": r.album,
              "duration_ms": r.duration_ms, "quality": r.quality,
              "format": r.format, "size": r.size, "source_url": r.source_url}
             for r in results]
    return match_candidates(source, dicts, auto, conditional, review)


def save_candidates(db: Session, track_id: int, ranked: list[dict]) -> dict[str, int]:
    """Replace candidates; return {provider_track_id: candidate_id} for all saved.

    Rejections survive re-searches: previously rejected keys are re-marked.
    """
    rejected_keys = {r[0] for r in db.query(DownloadCandidate.provider_track_id).filter(
        DownloadCandidate.track_id == track_id,
        DownloadCandidate.rejected_reason.is_not(None)).all()}
    db.query(DownloadCandidate).filter(DownloadCandidate.track_id == track_id).delete()
    mapping: dict[str, int] = {}
    for c in ranked:
        key = c["provider_track_id"]
        row = DownloadCandidate(track_id=track_id, provider=c["provider"],
                                provider_track_id=key,
                                title=c.get("title", ""), artist=c.get("artist", ""),
                                album=c.get("album", ""),
                                duration_ms=c.get("duration_ms"), quality=c.get("quality"),
                                format=c.get("format"), size=c.get("size"),
                                source_url=c.get("source_url"), score=c["score"])
        if key in rejected_keys:
            row.rejected_reason = "previously rejected"
        db.add(row)
        db.flush()
        mapping[key] = row.id
    for c in ranked:
        c["rejected"] = c["provider_track_id"] in rejected_keys
    return mapping


def rejected_keys(db: Session, track_id: int) -> set[str]:
    return {r[0] for r in db.query(DownloadCandidate.provider_track_id).filter(
        DownloadCandidate.track_id == track_id,
        DownloadCandidate.rejected_reason.is_not(None)).all()}
