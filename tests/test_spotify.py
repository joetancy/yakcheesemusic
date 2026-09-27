from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Playlist, PlaylistTrack
from app.providers.base import ProviderPlaylist, ProviderTrack
from app.providers.spotify import (
    extract_playlist_id, track_id_from_uri, parse_embed_json,
)


def test_extract_playlist_id():
    assert extract_playlist_id("https://open.spotify.com/playlist/7BkkyH1o2oEBA8nYiyllyj?si=x") == "7BkkyH1o2oEBA8nYiyllyj"
    assert extract_playlist_id("https://example.com/foo") is None


def test_track_id_from_uri():
    assert track_id_from_uri("spotify:track:65FftemJ1DbbZ45DUfHJXE") == "65FftemJ1DbbZ45DUfHJXE"


def _embed_payload():
    return {"props": {"pageProps": {"state": {"data": {"entity": {
        "id": "abc123", "name": "Korean",
        "trackList": [
            {"uri": "spotify:track:AAA", "title": "OMG", "subtitle": "NewJeans",
             "duration": 212253, "entityType": "track"},
            {"uri": "spotify:track:BBB", "title": "Super Shy", "subtitle": "NewJeans",
             "duration": 154000, "entityType": "track"},
            {"uri": "spotify:episode:CCC", "title": "Podcast", "subtitle": "Someone",
             "duration": 999, "entityType": "episode"},
        ]}}}}}}


def test_parse_embed_json():
    pl = parse_embed_json(_embed_payload())
    assert pl.name == "Korean"
    assert len(pl.tracks) == 2  # episode skipped
    assert pl.tracks[0].provider_track_id == "AAA"
    assert pl.tracks[0].artist == "NewJeans"
    assert pl.tracks[0].duration_ms == 212253


def test_parse_embed_json_error_page():
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="private"):
        parse_embed_json({"props": {"pageProps": {
            "status": 404, "title": "Page not found"}}})


def _pathfinder_item():
    return {
        "itemV2": {"__typename": "TrackResponseWrapper", "data": {
            "__typename": "Track", "uri": "spotify:track:XYZ",
            "name": "Ditto", "trackNumber": 3, "discNumber": 1,
            "trackDuration": {"totalMilliseconds": 185000},
            "artists": {"items": [{"profile": {"name": "NewJeans"}}]},
            "albumOfTrack": {"name": "OMG", "date": {"isoString": "2023-01-02T00:00:00Z"}},
        }},
        "itemV3": {"__typename": "EntityResponseWrapper"},
    }


def test_parse_pathfinder_item():
    from app.providers.spotify_web import _parse_item
    t = _parse_item(_pathfinder_item(), 7)
    assert t.provider_track_id == "XYZ"
    assert (t.title, t.artist, t.album) == ("Ditto", "NewJeans", "OMG")
    assert t.duration_ms == 185000
    assert (t.year, t.disc_number, t.track_number, t.position) == (2023, 1, 3, 7)


def test_parse_pathfinder_skips_episodes():
    from app.providers.spotify_web import _parse_item
    assert _parse_item({"itemV2": {"data": {"__typename": "Episode"}}}, 0) is None


def test_fetch_uses_canonical_url(monkeypatch):
    import app.providers.spotify_web as web
    seen = {}

    def fake_harvest(url, pid, timeout_s=90):
        seen["url"] = url
        return {"headers": {"authorization": "B", "client-token": "C",
                            "spotify-app-version": "V"},
                "operationName": "fetchPlaylist",
                "variables": {"uri": f"spotify:playlist:{pid}", "offset": 0,
                              "limit": 25},
                "extensions": {}}

    def fake_page(session, headers, payload):
        return {"data": {"playlistV2": {
            "name": "N", "content": {"totalCount": 0, "items": []}}}}

    monkeypatch.setattr(web, "_harvest", fake_harvest)
    monkeypatch.setattr(web, "_page", fake_page)
    pl = web.fetch_full_playlist(
        "https://open.spotify.com/playlist/abc123?si=xyz&utm_source=copy-link",
        "abc123")
    assert seen["url"] == "https://open.spotify.com/playlist/abc123"
    assert pl.url == "https://open.spotify.com/playlist/abc123"
    assert pl.complete is True


def _mem_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_scan_reconcile(monkeypatch):
    from app.providers.spotify import SpotifyProvider
    from app.sync.scan import scan_playlist

    db = _mem_db()
    p = Playlist(name="", provider="spotify",
                 url="https://open.spotify.com/playlist/abc123")
    db.add(p)
    db.commit()

    pl = ProviderPlaylist(provider="spotify", provider_playlist_id="abc123",
                          name="Korean", url="", track_count=2, tracks=[
        ProviderTrack(provider_track_id="AAA", title="OMG", artist="NewJeans", position=0),
        ProviderTrack(provider_track_id="BBB", title="Super Shy", artist="NewJeans", position=1),
    ])
    monkeypatch.setattr(SpotifyProvider, "get_playlist", lambda self, url: pl)
    r1 = scan_playlist(db, p.id)
    assert r1 == {"seen": 2, "added": 2, "removed": 0, "name": "Korean", "truncated": False}

    r2 = scan_playlist(db, p.id)  # idempotent rescan
    assert r2["added"] == 0 and r2["removed"] == 0

    pl2 = ProviderPlaylist(provider="spotify", provider_playlist_id="abc123",
                           name="Korean", url="", track_count=1, tracks=[
        ProviderTrack(provider_track_id="BBB", title="Super Shy", artist="NewJeans", position=0),
    ])
    monkeypatch.setattr(SpotifyProvider, "get_playlist", lambda self, url: pl2)
    r3 = scan_playlist(db, p.id)
    assert r3["added"] == 0 and r3["removed"] == 1
    remaining = db.query(PlaylistTrack).filter_by(active=True).all()
    assert len(remaining) == 1 and remaining[0].provider_track_id == "BBB"


def _sentinel_playlist(name="WebHarvest"):
    return ProviderPlaylist(provider="spotify", provider_playlist_id="abc123",
                            name=name, url="", track_count=1, tracks=[
        ProviderTrack(provider_track_id="AAA", title="OMG",
                      artist="NewJeans", position=0),
    ])


def test_bad_api_credentials_fall_through_to_web(monkeypatch):
    import app.providers.spotify as spotify
    from app.providers.spotify_web import SpotifyWebProvider
    monkeypatch.setattr(spotify, "_get_api_token",
                        lambda: (_ for _ in ()).throw(RuntimeError("400 Bad Request")))
    monkeypatch.setattr(SpotifyWebProvider, "get_playlist",
                        lambda self, url: _sentinel_playlist())
    pl = spotify.SpotifyProvider().get_playlist(
        "https://open.spotify.com/playlist/abc123")
    assert pl.name == "WebHarvest" and len(pl.tracks) == 1


def test_web_failure_falls_through_to_embed(monkeypatch):
    import app.providers.spotify as spotify
    from app.providers.spotify_web import SpotifyWebProvider
    monkeypatch.setattr(spotify, "_get_api_token", lambda: None)
    monkeypatch.setattr(SpotifyWebProvider, "get_playlist",
                        lambda self, url: (_ for _ in ()).throw(RuntimeError("no browser")))
    monkeypatch.setattr(spotify, "_fetch_via_embed",
                        lambda pid: _sentinel_playlist("Embed"))
    pl = spotify.SpotifyProvider().get_playlist(
        "https://open.spotify.com/playlist/abc123")
    assert pl.name == "Embed"
