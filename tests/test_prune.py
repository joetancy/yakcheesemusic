import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Playlist, Track, PlaylistTrack
from app.sync.prune import delete_track_file, prune_empty_parents


def _mem_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def _file(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("x")
    return path


def test_shared_file_kept():
    db = _mem_db()
    import tempfile
    music = tempfile.mkdtemp()
    shared = _file(os.path.join(music, "A", "shared.flac"))
    solo = _file(os.path.join(music, "B", "solo.flac"))
    pa = Playlist(name="A", provider="x", url="u1")
    pb = Playlist(name="B", provider="x", url="u2")
    db.add_all([pa, pb])
    db.commit()
    t1 = Track(title="shared", local_path=shared, status="completed")
    t2 = Track(title="solo", local_path=solo, status="completed")
    db.add_all([t1, t2])
    db.flush()
    db.add_all([
        PlaylistTrack(playlist_id=pa.id, track_id=t1.id, provider_track_id="s",
                      position=0, active=True),
        PlaylistTrack(playlist_id=pb.id, track_id=t1.id, provider_track_id="s",
                      position=0, active=True),
        PlaylistTrack(playlist_id=pa.id, track_id=t2.id, provider_track_id="o",
                      position=1, active=True),
    ])
    db.commit()

    assert delete_track_file(db, t1, pa.id, music) is None  # used by B
    assert os.path.exists(shared)
    assert delete_track_file(db, t2, pa.id, music) == solo  # only in A
    assert not os.path.exists(solo)
    assert not os.path.exists(os.path.join(music, "B"))  # empty dirs pruned
    assert os.path.exists(os.path.join(music, "A"))  # shared file still there


def test_prune_never_escapes_music_dir(tmp_path):
    outside = str(tmp_path / "outside")
    os.makedirs(outside)
    f = os.path.join(outside, "x.flac")
    open(f, "w").write("x")
    os.remove(f)
    prune_empty_parents(f, str(tmp_path / "music"))  # nothing under root
    assert os.path.exists(outside)  # untouched


def test_delete_endpoint_with_files(client, tmp_path, monkeypatch):
    import app.config
    music = str(tmp_path / "music")

    class _S:
        music_dir = music

    monkeypatch.setattr(app.config, "get_settings", lambda: _S())
    pid = client.post("/api/playlists", json={
        "url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}).json()["id"]
    # no tracks seeded on the isolated client DB: flag path is a no-op
    r = client.delete(f"/api/playlists/{pid}?delete_files=true")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "deleted_files": 0, "kept_files": 0}
