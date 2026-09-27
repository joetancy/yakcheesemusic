def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_create_playlist_validation(client):
    # invalid URL rejected
    r = client.post("/api/playlists", json={"url": "https://example.com/foo"})
    assert r.status_code == 400
    # spotify URL accepted (no network call on create)
    r = client.post("/api/playlists", json={
        "url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        "name": "test"})
    assert r.status_code == 200
    assert r.json()["provider"] == "spotify"


def test_create_playlist_normalizes_url(client):
    pid = client.post("/api/playlists", json={
        "url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc&utm_source=copy-link",
        "name": "test"}).json()["id"]
    assert client.get(f"/api/playlists/{pid}").json()["url"] == \
        "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"


def test_delete_playlist_cleans_orphans(client):
    pid = client.post("/api/playlists", json={
        "url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
        "name": "tmp"}).json()["id"]
    r = client.delete(f"/api/playlists/{pid}")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted_files": 0, "kept_files": 0}
    assert client.get(f"/api/playlists/{pid}").status_code == 404


import pytest as _pytest
from fastapi.testclient import TestClient as _TestClient
from sqlalchemy import create_engine as _create_engine
from sqlalchemy.orm import sessionmaker as _sessionmaker
from sqlalchemy.pool import StaticPool as _StaticPool

from app.db.database import Base as _Base, get_db as _get_db
from app.db.models import Track as _Track
from app.main import app as _app


@_pytest.fixture
def seeded_client():
    engine = _create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=_StaticPool)
    _Base.metadata.create_all(bind=engine)
    TestingSession = _sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = TestingSession()
    db.add_all([
        _Track(title="A", artist="X", status="needs_review"),
        _Track(title="B", artist="Y", status="failed"),
        _Track(title="C", artist="Z", status="completed"),
        _Track(title="D", artist="W", status="skipped"),
    ])
    db.commit()
    db.close()

    def override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    _app.dependency_overrides[_get_db] = override
    with _TestClient(_app) as c:
        yield c
    _app.dependency_overrides.clear()


def test_retry_all_resets_review_and_failed(seeded_client):
    r = seeded_client.post("/api/tracks/retry-all")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "reset": 2}
    assert len(seeded_client.get("/api/tracks", params={"status": "pending"}).json()) == 2
    assert seeded_client.get("/api/tracks", params={"status": "completed"}).json()[0]["title"] == "C"
    assert seeded_client.get("/api/tracks", params={"status": "skipped"}).json()[0]["title"] == "D"


def test_retry_single_track(seeded_client):
    tid = seeded_client.get("/api/tracks", params={"status": "failed"}).json()[0]["id"]
    r = seeded_client.post(f"/api/tracks/{tid}/retry")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "status": "pending"}
    assert seeded_client.post("/api/tracks/9999/retry").status_code == 404
    cid = seeded_client.get("/api/tracks", params={"status": "completed"}).json()[0]["id"]
    r = seeded_client.post(f"/api/tracks/{cid}/retry")
    assert r.status_code == 200  # completed tracks can opt back into search
    assert r.json() == {"ok": True, "status": "pending"}
    assert seeded_client.post(f"/api/tracks/{cid}/retry").status_code == 409


def test_search_accepts_custom_query(seeded_client, monkeypatch):
    import app.api.tracks as tracks_mod
    seen = {}

    def fake_run(tid, query=None, session_factory=None):
        seen["query"] = query

    monkeypatch.setattr(tracks_mod, "_run_search", fake_run)
    tid = seeded_client.get("/api/tracks", params={"status": "failed"}).json()[0]["id"]
    r = seeded_client.post(f"/api/tracks/{tid}/search", json={"query": "brent ii"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "status": "searching"}
    assert seen["query"] == "brent ii"
    r = seeded_client.post(f"/api/tracks/{tid}/search", json={})
    assert r.status_code == 409  # already searching


def test_run_search_empty_results(monkeypatch):
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm
    import app.sync.search as search_mod
    from app.api.tracks import _run_search
    from app.db.database import Base as _Base
    from app.db.models import Track as _T
    monkeypatch.setattr(search_mod, "search_candidates", lambda *a, **k: [])
    e = _ce("sqlite://", connect_args={"check_same_thread": False})
    _Base.metadata.create_all(bind=e)
    S = _sm(bind=e, future=True)
    db = S()
    db.add(_T(title="T", artist="A", status="searching"))
    db.commit()
    db.close()
    _run_search(1, None, session_factory=S)
    assert S().get(_T, 1).status == "needs_review"


def test_review_detail_paging_counts():
    from fastapi.testclient import TestClient as _TC
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm
    from sqlalchemy.pool import StaticPool as _SP
    from app.db.database import Base as _Base, get_db as _gdb
    from app.db.models import Track as _T, DownloadCandidate as _DC
    from app.main import app as _app
    e = _ce("sqlite://", connect_args={"check_same_thread": False}, poolclass=_SP)
    _Base.metadata.create_all(bind=e)
    S = _sm(bind=e, autoflush=False, autocommit=False, future=True)
    db = S()
    t = _T(title="T", artist="A", status="needs_review")
    db.add(t)
    db.commit()
    for i in range(25):
        db.add(_DC(track_id=t.id, provider="soulseek",
                   provider_track_id=f"u/f{i}.mp3", title=f"S{i}",
                   artist="A", score=80.0 - i))
    db.commit()
    db.close()

    def ov():
        s = S()
        try:
            yield s
        finally:
            s.close()

    _app.dependency_overrides[_gdb] = ov
    try:
        with _TC(_app) as c:
            r1 = c.get("/review/1")
            assert r1.status_code == 200
            assert "showing 10 of 25 usable" in r1.text
            assert "Show 10 more (15 remaining)" in r1.text
            assert r1.text.count("Use this") == 10
            r2 = c.get("/review/1", params={"shown": 30})
            assert "showing 25 of 25 usable" in r2.text
            assert "Show 10 more" not in r2.text
    finally:
        _app.dependency_overrides.clear()


def test_scan_endpoint_launches_job(seeded_client, monkeypatch):
    import app.sync.scan as scan_mod
    calls = []
    monkeypatch.setattr(scan_mod, "run_scan_job",
                        lambda sf, pid, jid: calls.append((pid, jid)))
    pid = seeded_client.post("/api/playlists", json={
        "url": "https://open.spotify.com/playlist/abc123",
        "name": "ScanMe"}).json()["id"]
    r = seeded_client.post(f"/api/playlists/{pid}/scan")
    assert r.status_code == 200
    assert r.json()["ok"] is True and "job_id" in r.json()
    assert calls and calls[0][0] == pid
    r = seeded_client.post(f"/api/playlists/{pid}/scan")
    assert r.status_code == 409  # scan job still running
    assert seeded_client.post("/api/playlists/9999/scan").status_code == 404


def test_run_scan_job_records_counts(monkeypatch):
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import sessionmaker as _sm
    from app.db.database import Base as _Base
    from app.db.models import Playlist as _P, SyncJob as _J
    from app.providers.base import ProviderPlaylist, ProviderTrack
    from app.providers.spotify import SpotifyProvider
    from app.sync.scan import run_scan_job
    monkeypatch.setattr(SpotifyProvider, "get_playlist", lambda self, url: ProviderPlaylist(
        provider="spotify", provider_playlist_id="abc", name="N", url="",
        track_count=1, tracks=[ProviderTrack(
            provider_track_id="AAA", title="T", artist="A", position=0)]))
    e = _ce("sqlite://", connect_args={"check_same_thread": False})
    _Base.metadata.create_all(bind=e)
    S = _sm(bind=e, future=True)
    db = S()
    db.add(_P(name="", provider="spotify", url="https://open.spotify.com/playlist/abc"))
    db.add(_J(playlist_id=1, status="running"))
    db.commit()
    db.close()
    run_scan_job(S, 1, 1)
    db = S()
    job = db.get(_J, 1)
    assert job.status == "success"
    assert (job.tracks_seen, job.tracks_added) == (1, 1)
    db.close()
