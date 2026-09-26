from app.downloaders.slskd import (
    _ext_of, describe_quality, file_to_result, parse_filename, AUDIO_EXTS,
)
from app.sync.library import sanitize, library_path


def test_ext_of():
    assert _ext_of(r"C:\Music\Artist\song.FLAC") == ".flac"
    assert _ext_of("/a/b/song.mp3") == ".mp3"
    assert _ext_of("noext") == ""
    assert ".exe" not in AUDIO_EXTS and ".flac" in AUDIO_EXTS


def test_describe_quality():
    q, f = describe_quality({"filename": "x.flac", "bitDepth": 16, "sampleRate": 44100})
    assert q == "FLAC 16/44.1" and f == "flac"
    q, f = describe_quality({"filename": "x.mp3", "bitRate": 320})
    assert q == "MP3 320" and f == "mp3"
    q, f = describe_quality({"filename": "x.flac"})
    assert q == "FLAC"


def test_file_to_result_filters_non_audio():
    assert file_to_result("u", {"filename": "song.exe", "size": 1}) is None
    r = file_to_result("u", {"filename": "song.flac", "size": 123, "length": 200})
    assert r and r.provider == "soulseek"
    assert r.provider_track_id == "u/song.flac"
    assert r.duration_ms == 200000


def test_parse_filename():
    assert parse_filename("02. Little Thing Called Love.flac") == ("", "Little Thing Called Love")
    assert parse_filename("FIFTY FIFTY - Called it Love.mp3") == ("FIFTY FIFTY", "Called it Love")
    assert parse_filename("@@lglyw\\0\\redferne\\album\\01 - Song.flac") == ("", "Song")


def test_file_to_result_parses_remote_metadata():
    r = file_to_result("u", {"filename": "FIFTY FIFTY - Called it Love.flac", "size": 5})
    assert (r.title, r.artist) == ("Called it Love", "FIFTY FIFTY")
    assert r.source_url.startswith("soulseek://")


def test_sanitize():
    assert sanitize('AC/DC: Live?') == 'AC_DC_ Live_'
    assert sanitize('  spaced   out  ') == 'spaced out'
    assert sanitize('') == 'Unknown'


def test_library_path(tmp_path):
    p = library_path(str(tmp_path), "NewJeans", "Get Up", "OMG", ".flac", 1)
    assert str(p).endswith("NewJeans/Get Up/01 - OMG.flac")
    p2 = library_path(str(tmp_path), "A/B", "C", "T", "mp3", None)
    assert p2.name == "T.mp3" and p2.parent.parent.name == "A_B"


def test_human_size():
    from app.sync.library import human_size
    assert human_size(0) == "0 B"
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"
    assert human_size(int(1.5 * 1024 ** 3)) == "1.5 GB"


def test_playlist_usage(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    from app.db.models import Playlist, Track, PlaylistTrack
    from app.sync.library import playlist_usage
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, future=True)()
    p = Playlist(name="P", provider="spotify", url="u")
    db.add(p)
    db.commit()
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x" * 2048)
    t1 = Track(title="A", status="completed", local_path=str(f))
    t2 = Track(title="B", status="pending")
    db.add_all([t1, t2])
    db.flush()
    db.add_all([
        PlaylistTrack(playlist_id=p.id, track_id=t1.id, provider_track_id="1",
                      position=0, active=True),
        PlaylistTrack(playlist_id=p.id, track_id=t2.id, provider_track_id="2",
                      position=1, active=True),
    ])
    db.commit()
    assert playlist_usage(db, p.id) == (2048, 1)


def test_quality_label():
    from app.db.models import Track
    t = Track(title="x", format="flac", bit_depth=16, sample_rate=44100)
    assert t.quality_label == "FLAC 16/44.1"
    t = Track(title="x", format="mp3", bitrate=320)
    assert t.quality_label == "MP3 320"
    t = Track(title="x", format="mp3")
    assert t.quality_label == "MP3"
    t = Track(title="x")
    assert t.quality_label is None


def test_probe_mp3(tmp_path):
    import wave
    from app.audio.probe import probe_audio
    wf = wave.open(str(tmp_path / "t.wav"), "wb")
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(44100)
    wf.writeframes(b"\x00" * 44100 * 2 * 2)
    wf.close()
    info = probe_audio(str(tmp_path / "t.wav"))
    assert info.get("sample_rate") == 44100
    assert info.get("bit_depth") == 16
