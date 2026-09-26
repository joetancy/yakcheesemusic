import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Playlist, Track, PlaylistTrack
from app.sync.m3u import generate_m3u, m3u_path


def _mem_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def _seed(db, tmpdir):
    p = Playlist(name="Korean", provider="youtube_music", url="http://x/?list=1")
    db.add(p)
    db.commit()
    f1 = os.path.join(tmpdir, "A", "song1.flac")
    os.makedirs(os.path.dirname(f1))
    open(f1, "w").write("x")
    t1 = Track(title="Song1", artist="A", duration_ms=200000,
               local_path=f1, format="flac", status="completed")
    t2 = Track(title="Song2", artist="B", status="pending")
    db.add_all([t1, t2])
    db.flush()
    db.add_all([
        PlaylistTrack(playlist_id=p.id, track_id=t1.id, provider_track_id="v1",
                      position=1, source_title="Song1", source_artist="A", active=True),
        PlaylistTrack(playlist_id=p.id, track_id=t2.id, provider_track_id="v2",
                      position=0, source_title="Song2", source_artist="B", active=True),
        PlaylistTrack(playlist_id=p.id, track_id=None, provider_track_id="v3",
                      position=2, source_title="Gone", source_artist="C", active=False),
    ])
    db.commit()
    return p


def test_generate_m3u(tmp_path):
    db = _mem_db()
    music = str(tmp_path / "music")
    plist = os.path.join(music, "_Playlists")
    p = _seed(db, music)
    res = generate_m3u(db, p.id, music, plist)
    assert res == {"path": os.path.join(plist, "Korean.m3u"),
                   "included": 1, "missing": 1}
    text = open(res["path"]).read()
    assert text.startswith("#EXTM3U\n#PLAYLIST:Korean\n")
    assert "#EXTINF:200,A - Song1" in text
    assert "../A/song1.flac" in text
    assert "Song2" not in text  # not downloaded -> excluded


def test_m3u_collision_uses_id(tmp_path):
    db = _mem_db()
    p = _seed(db, str(tmp_path))
    plist = str(tmp_path / "pl")
    os.makedirs(plist)
    open(os.path.join(plist, "Korean.m3u"), "w").write("old")
    assert m3u_path(plist, p).name == f"Korean [{p.id}].m3u"
