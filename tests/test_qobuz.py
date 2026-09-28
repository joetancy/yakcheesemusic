"""Qobuz fallback tests (streamrip itself is mocked — no creds needed)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import DownloadCandidate, Playlist, PlaylistTrack, SyncJob, Track


def _mem_factory():
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)


def test_configured_false_without_creds():
    from app.downloaders import streamrip_qobuz as sq
    assert sq.configured() is False
    assert sq.ensure_config() is None


def test_ensure_config_renders_and_preserves(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import app.downloaders.streamrip_qobuz as sq
    monkeypatch.setattr(sq, "get_settings", lambda: SimpleNamespace(
        qobuz_email="u@x.com", qobuz_password="pw", qobuz_quality=3))
    monkeypatch.setattr(sq, "CONFIG_PATH", str(tmp_path / "sr.toml"))
    monkeypatch.setattr(sq, "STAGING_DIR", str(tmp_path / "st"))
    p = sq.ensure_config()
    text = open(p).read()
    assert 'email_or_userid = "u@x.com"' in text
    import hashlib
    assert hashlib.md5(b"pw").hexdigest() in text
    assert "quality = 3" in text
    # second run preserves streamrip's own fields
    with open(p, "a") as f:
        f.write("")
    import re
    text2 = re.sub(r'app_id = ""', 'app_id = "12345"', text)
    text2 = text2.replace("secrets = []", 'secrets = ["s1"]')
    open(p, "w").write(text2)
    sq.ensure_config()
    text3 = open(p).read()
    assert 'app_id = "12345"' in text3 and 'secrets = ["s1"]' in text3


def test_track_to_result_mapping():
    from app.downloaders.streamrip_qobuz import _track_to_result
    r = _track_to_result({"id": 42, "title": "Ditto",
                          "performer": {"name": "NewJeans"},
                          "album": {"title": "OMG"}, "duration": 185})
    assert (r.provider, r.provider_track_id) == ("qobuz", "qobuz:42")
    assert (r.title, r.artist, r.album) == ("Ditto", "NewJeans", "OMG")
    assert r.duration_ms == 185000
    assert _track_to_result({}) is None
    assert _track_to_result(None) is None


def test_downloader_dispatch(monkeypatch):
    import app.downloaders.slskd as slskd_mod
    import app.downloaders.streamrip_qobuz as sq_mod
    import app.downloaders.streamrip_deezer as dz_mod
    from app.sync.download import _downloader_for, _source_url
    monkeypatch.setattr(slskd_mod, "SlskdDownloader", lambda *a, **k: "SLSKD")
    monkeypatch.setattr(sq_mod, "QobuzDownloader", lambda *a, **k: "QOBUZ")
    monkeypatch.setattr(dz_mod, "DeezerDownloader", lambda *a, **k: "DEEZER")
    assert _downloader_for("soulseek") == "SLSKD"
    assert _downloader_for("qobuz") == "QOBUZ"
    assert _downloader_for("deezer") == "DEEZER"
    assert _downloader_for(None) == "SLSKD"
    assert _source_url("qobuz", "qobuz:1") == "qobuz://qobuz:1"
    assert _source_url("deezer", "deezer:2") == "deezer://deezer:2"
    assert _source_url("soulseek", "u/f") == "soulseek://u/f"


def _seed_eligibility(factory):
    db = factory()
    p = Playlist(name="P", provider="spotify", url="u")
    db.add(p)
    db.flush()
    pid = p.id
    a = Track(title="NoCands", artist="A", status="pending")
    b = Track(title="Usable", artist="A", status="pending")
    c = Track(title="Done", artist="A", status="completed")
    d = Track(title="Rejected", artist="A", status="failed")
    db.add_all([a, b, c, d])
    db.flush()
    db.add(DownloadCandidate(track_id=b.id, provider="soulseek",
                             provider_track_id="u/f.mp3", title="x",
                             score=50.0))
    db.add(DownloadCandidate(track_id=d.id, provider="soulseek",
                             provider_track_id="u/g.mp3", title="x",
                             score=10.0, rejected_reason="rejected by user"))
    for t in (a, b, c, d):
        db.add(PlaylistTrack(playlist_id=p.id, track_id=t.id,
                             provider_track_id=t.title, position=0, active=True))
    ids = {t.title: t.id for t in (a, b, c, d)}
    db.commit()
    db.close()
    return pid, ids


def test_fallback_only_attempts_exhausted(monkeypatch):
    import app.downloaders.streamrip_qobuz as sq_mod
    import app.sync.qobuz_fallback as fb
    monkeypatch.setattr(sq_mod, "configured", lambda: True)
    seen = []

    class FakeQD:
        def search(self, *a, **k):
            seen.append(a)
            return []

    monkeypatch.setattr(sq_mod, "QobuzDownloader", FakeQD)
    factory = _mem_factory()
    pid, ids = _seed_eligibility(factory)
    db = factory()
    db.add(SyncJob(playlist_id=pid, status="running"))
    db.commit()
    jid = db.query(SyncJob).first().id
    db.close()
    out = fb.run_qobuz_fallback(factory, jid, pid)
    # NoCands (no rows) + Rejected (only rejected rows) attempted; others skipped
    assert out == {"attempted": 2, "downloaded": 0, "review": 0}
    assert len(seen) == 2


def test_fallback_disabled_without_creds():
    import app.sync.qobuz_fallback as fb
    factory = _mem_factory()
    assert fb.run_qobuz_fallback(factory, 1, 1) == {
        "attempted": 0, "downloaded": 0, "review": 0}


def test_deezer_mapping_and_config(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import app.downloaders.streamrip_deezer as dz
    assert dz.configured() is False
    assert dz.ensure_config() is None
    r = dz._track_to_result({"id": 7, "title": "T", "artist": {"name": "A"},
                             "album": {"title": "B"}, "duration": 200})
    assert (r.provider, r.provider_track_id) == ("deezer", "deezer:7")
    assert (r.title, r.artist, r.album) == ("T", "A", "B")
    assert r.duration_ms == 200000
    assert dz._track_to_result({}) is None
    monkeypatch.setattr(dz, "get_settings", lambda: SimpleNamespace(
        deezer_arl="abc123", deezer_quality=2))
    monkeypatch.setattr(dz, "CONFIG_PATH", str(tmp_path / "sr.toml"))
    monkeypatch.setattr(dz, "STAGING_DIR", str(tmp_path / "st"))
    p = dz.ensure_config()
    assert 'arl = "abc123"' in open(p).read()


def test_deezer_ensure_preserves_qobuz(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import app.downloaders.streamrip_deezer as dz
    monkeypatch.setattr(dz, "get_settings", lambda: SimpleNamespace(
        deezer_arl="abc123", deezer_quality=2))
    monkeypatch.setattr(dz, "CONFIG_PATH", str(tmp_path / "sr.toml"))
    monkeypatch.setattr(dz, "STAGING_DIR", str(tmp_path / "st"))
    open(str(tmp_path / "sr.toml"), "w").write(
        '[qobuz]\nemail_or_userid = "u@x.com"\n')
    dz.ensure_config()
    text = open(str(tmp_path / "sr.toml")).read()
    assert 'email_or_userid = "u@x.com"' in text
    assert 'arl = "abc123"' in text


def test_deezer_fallback_attempts_exhausted(monkeypatch):
    import app.downloaders.streamrip_deezer as dz_mod
    import app.sync.qobuz_fallback as fb
    monkeypatch.setattr(dz_mod, "configured", lambda: True)
    seen = []

    class FakeDD:
        def search(self, *a, **k):
            seen.append(a)
            return []

    monkeypatch.setattr(dz_mod, "DeezerDownloader", FakeDD)
    factory = _mem_factory()
    pid, ids = _seed_eligibility(factory)
    db = factory()
    db.add(SyncJob(playlist_id=pid, status="running"))
    db.commit()
    jid = db.query(SyncJob).first().id
    db.close()
    out = fb.run_deezer_fallback(factory, jid, pid)
    assert out == {"attempted": 2, "downloaded": 0, "review": 0}
    assert len(seen) == 2
