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
