"""Full Spotify playlist reads without API keys or Premium.

How: launch headless Chromium on the public playlist page, capture the
page's own live `fetchPlaylist` pathfinder request (operation hash, headers,
anonymous bearer token — all harvested fresh, nothing hardcoded), then replay
it with paginated offsets over plain HTTPS. Falls back to embed on any error.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from app.providers.base import PlaylistProvider, ProviderPlaylist, ProviderTrack

log = logging.getLogger("music-sync")
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "Chrome/120 Safari/537.36")
_PATHFINDER = "https://api-partner.spotify.com/pathfinder/v2/query"


def _harvest(url: str, playlist_id: str, timeout_s: int = 90) -> dict:
    """Return {headers, operationName, variables, extensions} from live traffic."""
    from playwright.sync_api import sync_playwright

    found: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(user_agent=_UA)

            def on_request(req) -> None:
                if "pathfinder" not in req.url or req.method != "POST":
                    return
                try:
                    body = req.post_data or ""
                except Exception:
                    return
                if f"spotify:playlist:{playlist_id}" not in body:
                    return
                try:
                    data = json.loads(body)
                except Exception:
                    return
                if data.get("operationName") == "fetchPlaylist" and "offset" in (
                        data.get("variables") or {}):
                    found.update({
                        "headers": {k: v for k, v in req.headers.items()
                                    if k.lower() in ("authorization", "client-token",
                                                     "spotify-app-version")},
                        "operationName": data["operationName"],
                        "variables": data["variables"],
                        "extensions": data["extensions"],
                    })

            page.on("request", on_request)
            page.goto(url, timeout=60000)
            deadline = time.time() + timeout_s
            while not found and time.time() < deadline:
                try:
                    page.wait_for_selector('[data-testid="tracklist-row"]', timeout=5000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
        finally:
            browser.close()
    if not found:
        raise RuntimeError("no fetchPlaylist request observed")
    return found


def _parse_item(item: dict, position: int) -> ProviderTrack | None:
    data = ((item.get("itemV2") or {}).get("data")) or {}
    if data.get("__typename") != "Track":
        return None  # episode / unavailable entry
    uri = data.get("uri", "")
    artists = ", ".join(
        (a.get("profile") or {}).get("name", "")
        for a in ((data.get("artists") or {}).get("items") or []))
    album = (data.get("albumOfTrack") or {}).get("name", "")
    duration = (data.get("trackDuration") or {}).get("totalMilliseconds")
    date = ((data.get("albumOfTrack") or {}).get("date") or {}).get("isoString", "")
    year = int(date[:4]) if date and date[:4].isdigit() else None
    return ProviderTrack(
        provider_track_id=uri.split(":")[-1],
        title=data.get("name", ""),
        artist=artists,
        album=album,
        duration_ms=duration,
        year=year,
        disc_number=data.get("discNumber"),
        track_number=data.get("trackNumber"),
        position=position,
    )


def _page(session: httpx.Client, headers: dict, payload: dict) -> dict:
    r = session.post(_PATHFINDER, json=payload, headers=headers, timeout=30)
    if r.status_code in (401, 403):
        raise RuntimeError(f"pathfinder rejected harvested credentials: {r.status_code}")
    r.raise_for_status()
    return r.json()


def fetch_full_playlist(url: str, playlist_id: str) -> ProviderPlaylist:
    harvested = _harvest(url, playlist_id)
    headers = {"authorization": harvested["headers"]["authorization"],
               "client-token": harvested["headers"]["client-token"],
               "spotify-app-version": harvested["headers"].get("spotify-app-version", ""),
               "content-type": "application/json",
               "User-Agent": _UA,
               "Origin": "https://open.spotify.com",
               "Referer": "https://open.spotify.com/"}
    base_vars = dict(harvested["variables"])
    limit = int(base_vars.get("limit") or 25)
    tracks: list[ProviderTrack] = []
    name = playlist_id
    total = None
    with httpx.Client() as session:
        offset = 0
        while True:
            variables = dict(base_vars)
            variables["offset"] = offset
            data = _page(session, headers, {
                "operationName": harvested["operationName"],
                "variables": variables,
                "extensions": harvested["extensions"]})
            pl = (data.get("data") or {}).get("playlistV2") or {}
            name = pl.get("name", name)
            content = pl.get("content") or {}
            if total is None:
                total = content.get("totalCount", 0)
            items = content.get("items") or []
            if not items:
                break
            for item in items:
                t = _parse_item(item, len(tracks))
                if t:
                    tracks.append(t)
            offset += len(items)
            if total is not None and offset >= total:
                break
            if len(items) < limit:
                break
    return ProviderPlaylist(provider="spotify", provider_playlist_id=playlist_id,
                            name=name, url=url,
                            track_count=total if total is not None else len(tracks),
                            tracks=tracks, complete=True)


class SpotifyWebProvider(PlaylistProvider):
    """No-key full reads via the public web page. Last resort before embed."""
    provider_name = "spotify"

    def validate_url(self, url: str) -> bool:
        from app.providers.spotify import extract_playlist_id
        return extract_playlist_id(url) is not None

    def get_playlist(self, url: str) -> ProviderPlaylist:
        from app.providers.spotify import extract_playlist_id
        pid = extract_playlist_id(url)
        if not pid:
            raise ValueError(f"Not a Spotify playlist URL: {url}")
        return fetch_full_playlist(url, pid)

    def get_tracks(self, url: str) -> list[ProviderTrack]:
        return self.get_playlist(url).tracks
