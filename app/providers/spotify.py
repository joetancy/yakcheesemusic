"""Spotify playlist provider.

Two paths (no user login required):
1. Spotify Web API with client-credentials (needs SPOTIFY_CLIENT_ID/SECRET env).
   Full track list with pagination, ISRCs included.
2. Embed-page fallback (no credentials): first 100 tracks, no ISRC, no total.
"""
from __future__ import annotations

import base64
import re
import time

import httpx

from app.config import get_settings
from app.providers.base import PlaylistProvider, ProviderPlaylist, ProviderTrack

_SPOTIFY_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?playlist/([A-Za-z0-9]+)")
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

_token_cache: dict = {"token": "", "expires_at": 0.0}


def extract_playlist_id(url: str) -> str | None:
    m = _SPOTIFY_RE.search(url or "")
    return m.group(1) if m else None


def track_id_from_uri(uri: str) -> str:
    return (uri or "").split(":")[-1]


def _get_api_token() -> str | None:
    s = get_settings()
    if not (s.spotify_client_id and s.spotify_client_secret):
        return None
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    basic = base64.b64encode(f"{s.spotify_client_id}:{s.spotify_client_secret}".encode()).decode()
    r = httpx.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return _token_cache["token"]


def _map_api_item(item: dict, position: int) -> ProviderTrack | None:
    track = (item or {}).get("track") or {}
    if not track.get("id"):
        return None  # local/deleted entry
    artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
    album = (track.get("album") or {}).get("name", "")
    return ProviderTrack(
        provider_track_id=track["id"],
        title=track.get("name", ""),
        artist=artists,
        album=album,
        duration_ms=track.get("duration_ms"),
        isrc=(track.get("external_ids") or {}).get("isrc"),
        position=position,
    )


def _fetch_via_api(playlist_id: str) -> ProviderPlaylist:
    token = _get_api_token()
    assert token, "no Spotify API credentials"
    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.get(f"https://api.spotify.com/v1/playlists/{playlist_id}?fields=name",
                  headers=headers, timeout=20)
    r.raise_for_status()
    name = r.json().get("name", playlist_id)
    tracks: list[ProviderTrack] = []
    url = (f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
           "?fields=items(track(id,name,artists(name),album(name),duration_ms,external_ids(isrc))),next,total"
           "&limit=100")
    position = 0
    total = 0
    while url:
        resp = httpx.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total", total)
        for item in data.get("items", []):
            t = _map_api_item(item, position)
            position += 1
            if t:
                t.position = len(tracks)
                tracks.append(t)
        url = data.get("next")
    return ProviderPlaylist(provider="spotify", provider_playlist_id=playlist_id,
                            name=name, url="", track_count=total, tracks=tracks)


def parse_embed_json(data: dict) -> ProviderPlaylist:
    """Parse an already-decoded __NEXT_DATA__ payload from the embed page."""
    entity = data["props"]["pageProps"]["state"]["data"]["entity"]
    pid = entity.get("id", "")
    tracks = []
    for item in entity.get("trackList", []):
        if item.get("entityType") != "track":
            continue
        tracks.append(ProviderTrack(
            provider_track_id=track_id_from_uri(item.get("uri", "")),
            title=item.get("title", ""),
            artist=item.get("subtitle", ""),
            album="",
            duration_ms=item.get("duration"),
            position=len(tracks),
        ))
    return ProviderPlaylist(provider="spotify", provider_playlist_id=pid,
                            name=entity.get("name", pid), url="",
                            track_count=len(tracks), tracks=tracks)


def _fetch_via_embed(playlist_id: str) -> ProviderPlaylist:
    r = httpx.get(f"https://open.spotify.com/embed/playlist/{playlist_id}",
                  headers={"User-Agent": _UA}, timeout=20)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  r.text, re.S)
    if not m:
        raise RuntimeError("Spotify embed page did not contain track data")
    import json
    pl = parse_embed_json(json.loads(m.group(1)))
    pl.url = f"https://open.spotify.com/playlist/{playlist_id}"
    pl.complete = False  # embed page carries max 100 tracks
    return pl


class SpotifyProvider(PlaylistProvider):
    provider_name = "spotify"

    def validate_url(self, url: str) -> bool:
        return extract_playlist_id(url) is not None

    def get_playlist(self, url: str) -> ProviderPlaylist:
        import logging
        from app.providers.spotify_web import SpotifyWebProvider
        pid = extract_playlist_id(url)
        if not pid:
            raise ValueError(f"Not a Spotify playlist URL: {url}")
        try:
            if _get_api_token():
                try:
                    return _fetch_via_api(pid)
                except Exception as e:
                    # e.g. 403 when the app owner's account has no Premium
                    logging.getLogger("yakcheesemusic").warning(
                        "Spotify API failed (%s), trying web harvest", e)
        except Exception as e:
            # e.g. 400 from /api/token on bad credentials — degrade, don't die
            logging.getLogger("yakcheesemusic").warning(
                "Spotify API unavailable (%s), trying web harvest", e)
        try:
            return SpotifyWebProvider().get_playlist(url)
        except Exception as e:
            logging.getLogger("yakcheesemusic").warning(
                "Spotify web harvest failed (%s), using embed fallback", e)
        return _fetch_via_embed(pid)  # capped at 100 tracks

    def get_tracks(self, url: str) -> list[ProviderTrack]:
        return self.get_playlist(url).tracks


def get_provider() -> SpotifyProvider:
    return SpotifyProvider()
