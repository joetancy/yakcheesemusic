"""Candidate search + scoring shared by the API and batch sync."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from app.db.models import Track, DownloadCandidate
from app.downloaders.slskd import SlskdDownloader
from app.matching.matcher import match_candidates

log = logging.getLogger("yakcheesemusic")


def search_candidates(
    track: Track,
    auto: float = 90.0,
    conditional: float = 80.0,
    review: float = 65.0,
    query: str | None = None,
) -> list[dict]:
    """Search sources and return ranked candidates with scores and verdicts."""
    results = SlskdDownloader().search(
        track.title, track.artist, track.album or "", query=query
    )
    source = {
        "title": track.title,
        "artist": track.artist,
        "album": track.album or "",
        "duration_ms": track.duration_ms,
        "isrc": track.isrc,
    }
    dicts = [
        {
            "provider": r.provider,
            "provider_track_id": r.provider_track_id,
            "title": r.title,
            "artist": r.artist,
            "album": r.album,
            "duration_ms": r.duration_ms,
            "quality": r.quality,
            "format": r.format,
            "size": r.size,
            "source_url": r.source_url,
        }
        for r in results
    ]
    return match_candidates(source, dicts, auto, conditional, review)


def search_musicdl_candidates(
    track: Track, auto: float = 90.0, conditional: float = 80.0, review: float = 65.0
) -> list[dict]:
    """Search musicdl and score candidates with the configured thresholds."""
    from app.downloaders.musicdl import MusicdlDownloader

    results = MusicdlDownloader().search(track.title, track.artist, track.album or "")
    source = {
        "title": track.title,
        "artist": track.artist,
        "album": track.album or "",
        "duration_ms": track.duration_ms,
        "isrc": track.isrc,
    }
    candidates = [
        {
            "provider": r.provider,
            "provider_track_id": r.provider_track_id,
            "title": r.title,
            "artist": r.artist,
            "album": r.album,
            "duration_ms": r.duration_ms,
            "quality": r.quality,
            "format": r.format,
            "size": r.size,
            "source_url": r.source_url,
        }
        for r in results
    ]
    return match_candidates(source, candidates, auto, conditional, review)


def search_all_candidates(
    track: Track,
    auto: float = 90.0,
    conditional: float = 80.0,
    review: float = 65.0,
    *,
    enabled_sources: set[str],
    query: str | None = None,
) -> list[dict]:
    """Search every enabled, configured provider and rank one shared pool."""
    from types import SimpleNamespace

    # Capture ORM-backed values before the provider worker threads start.
    track = SimpleNamespace(
        id=track.id,
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration_ms=track.duration_ms,
        isrc=track.isrc,
    )
    searches = []
    if "slskd" in enabled_sources:
        def search_slskd():
            kwargs = {"query": query} if query else {}
            return search_candidates(track, auto, conditional, review, **kwargs)

        searches.append(("slskd", search_slskd))
    if "musicdl" in enabled_sources:
        searches.append(
            (
                "musicdl",
                lambda: search_musicdl_candidates(
                    track, auto, conditional, review
                ),
            )
        )

    # Credential-backed providers are still first-class sources; they simply
    # stay out of the pool until configured and enabled.
    if "qobuz" in enabled_sources:
        from app.downloaders.streamrip_qobuz import QobuzDownloader, configured

        if configured():
            searches.append(
                ("qobuz", lambda: _search_downloader(QobuzDownloader, track, query))
            )
    if "deezer" in enabled_sources:
        from app.downloaders.streamrip_deezer import DeezerDownloader, configured

        if configured():
            searches.append(
                ("deezer", lambda: _search_downloader(DeezerDownloader, track, query))
            )
    if "flac_music" in enabled_sources:
        from app.downloaders.flac_music import FlacMusicDownloader

        searches.append(
            (
                "flac_music",
                lambda: _search_downloader(FlacMusicDownloader, track, query),
            )
        )

    results_by_provider = {}
    if searches:
        with ThreadPoolExecutor(
            max_workers=len(searches), thread_name_prefix="provider-search"
        ) as executor:
            futures = {
                executor.submit(search): provider for provider, search in searches
            }
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    results_by_provider[provider] = future.result()
                except Exception as e:
                    log.warning(
                        "track=%s action=%s-search status=error error=%s",
                        track.id,
                        provider,
                        e,
                    )

    candidates = [
        candidate
        for provider, _ in searches
        for candidate in results_by_provider.get(provider, [])
    ]

    # The provider-specific helpers above may already have scored their
    # results. Re-score the merged list so all sources use the same catalogue
    # data and thresholds.
    source = {
        "title": track.title,
        "artist": track.artist,
        "album": track.album or "",
        "duration_ms": track.duration_ms,
        "isrc": track.isrc,
    }
    return match_candidates(source, candidates, auto, conditional, review)


def _search_downloader(downloader_cls, track, query: str | None) -> list[dict]:
    results = downloader_cls().search(
        track.title, track.artist, track.album or "", query=query
    )
    return [
        {
            "provider": r.provider,
            "provider_track_id": r.provider_track_id,
            "title": r.title,
            "artist": r.artist,
            "album": r.album,
            "duration_ms": r.duration_ms,
            "quality": r.quality,
            "format": r.format,
            "size": r.size,
            "source_url": r.source_url,
        }
        for r in results
    ]


def save_candidates(db: Session, track_id: int, ranked: list[dict]) -> dict[str, int]:
    """Replace candidates; return {provider_track_id: candidate_id} for all saved.

    Rejections survive re-searches: previously rejected keys are re-marked.
    """
    rejected_keys = {
        r[0]
        for r in db.query(DownloadCandidate.provider_track_id)
        .filter(
            DownloadCandidate.track_id == track_id,
            DownloadCandidate.rejected_reason.is_not(None),
        )
        .all()
    }
    db.query(DownloadCandidate).filter(DownloadCandidate.track_id == track_id).delete()
    mapping: dict[str, int] = {}
    for c in ranked:
        key = c["provider_track_id"]
        row = DownloadCandidate(
            track_id=track_id,
            provider=c["provider"],
            provider_track_id=key,
            title=c.get("title", ""),
            artist=c.get("artist", ""),
            album=c.get("album", ""),
            duration_ms=c.get("duration_ms"),
            quality=c.get("quality"),
            format=c.get("format"),
            size=c.get("size"),
            source_url=c.get("source_url"),
            score=c["score"],
        )
        if key in rejected_keys:
            row.rejected_reason = "previously rejected"
        db.add(row)
        db.flush()
        mapping[key] = row.id
    for c in ranked:
        c["rejected"] = c["provider_track_id"] in rejected_keys
    return mapping


def rejected_keys(db: Session, track_id: int) -> set[str]:
    return {
        r[0]
        for r in db.query(DownloadCandidate.provider_track_id)
        .filter(
            DownloadCandidate.track_id == track_id,
            DownloadCandidate.rejected_reason.is_not(None),
        )
        .all()
    }
