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
    import app.sync.search as search_mod
    seen = {}

    def fake_search(t, auto, conditional, review, query=None):
        seen["query"] = query
        return []

    monkeypatch.setattr(search_mod, "search_candidates", fake_search)
    tid = seeded_client.get("/api/tracks", params={"status": "failed"}).json()[0]["id"]
    r = seeded_client.post(f"/api/tracks/{tid}/search", json={"query": "brent ii"})
    assert r.status_code == 200
    assert seen["query"] == "brent ii"
    assert r.json()["status"] == "needs_review"


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
