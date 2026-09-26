from app.metadata.resolve import (
    is_junk, extract_album_from_path, resolve_metadata,
)
from app.metadata.tags import read_tags


def test_is_junk():
    assert is_junk(None) and is_junk("") and is_junk("Unknown Album")
    assert not is_junk("Get Up")


def test_extract_album_from_path():
    assert extract_album_from_path(
        "user/@@x/2026 - CLICK/01 - CLICK.flac", "JISOO") == "CLICK"
    # parent dir is the artist, not an album
    assert extract_album_from_path("user/JISOO/JISOO - CLICK.flac", "JISOO") is None
    assert extract_album_from_path("single.flac", "A") is None


def test_resolve_prefers_embedded_album():
    meta = resolve_metadata(
        {"title": "CLICK", "artist": "JISOO", "album": "", "year": None,
         "track_number": None},
        {"artist": None, "album": "CLICK", "title": None,
         "track_number": 1, "year": 2026},
        "u/whatever/x.flac")
    assert meta == {"artist": "JISOO", "title": "CLICK", "album": "CLICK",
                    "track_number": 1, "year": 2026}


def test_resolve_ignores_junk_falls_to_path_then_catalogue():
    meta = resolve_metadata(
        {"title": "T", "artist": "A", "album": "CatAlbum", "year": None,
         "track_number": None},
        {"artist": None, "album": "Unknown Album", "title": None,
         "track_number": None, "year": None},
        "u/2024 - PathAlbum/01 - T.flac")
    assert meta["album"] == "PathAlbum"
    meta2 = resolve_metadata(
        {"title": "T", "artist": "A", "album": "CatAlbum", "year": None,
         "track_number": None},
        {"artist": None, "album": None, "title": None,
         "track_number": None, "year": None},
        "u/x.flac")
    assert meta2["album"] == "CatAlbum"


def test_resolve_catalogue_identity_wins():
    meta = resolve_metadata(
        {"title": "Ditto", "artist": "NewJeans", "album": "", "year": None,
         "track_number": None},
        {"artist": "WRONG", "album": "Get Up", "title": "WRONG",
         "track_number": 3, "year": 2022},
        "u/x.mp3")
    assert meta["artist"] == "NewJeans" and meta["title"] == "Ditto"
    assert meta["album"] == "Get Up" and meta["track_number"] == 3


def test_read_tags_missing_file():
    assert read_tags("/nonexistent/x.mp3") == {
        "artist": None, "album": None, "title": None,
        "track_number": None, "year": None}


def test_read_tags_id3(tmp_path):
    from mutagen.id3 import ID3, TIT2, TALB, TPE1, TRCK
    p = str(tmp_path / "t.mp3")
    tags = ID3()
    tags.add(TIT2(text="Song"))
    tags.add(TALB(text="Album"))
    tags.add(TPE1(text="Artist"))
    tags.add(TRCK(text="4/12"))
    tags.save(p)
    out = read_tags(p)
    assert out["title"] == "Song" and out["album"] == "Album"
    assert out["artist"] == "Artist" and out["track_number"] == 4


def test_write_tags_mp3_roundtrip(tmp_path):
    from app.metadata.tagging import write_tags
    p = str(tmp_path / "song.mp3")
    open(p, "wb").write(b"\x00" * 128)
    assert write_tags(p, {"title": "Ditto", "artist": "NewJeans",
                          "album": "Get Up", "tracknumber": 2,
                          "date": 2023, "isrc": "ABC123"}) is True
    out = read_tags(p)
    assert out == {"artist": "NewJeans", "album": "Get Up", "title": "Ditto",
                   "track_number": 2, "year": 2023}


def test_write_tags_overwrites_sharer_tags_but_keeps_others(tmp_path):
    from mutagen.id3 import ID3, TIT2, TPE1, TXXX
    from app.metadata.tagging import write_tags
    p = str(tmp_path / "song.mp3")
    tags = ID3()
    tags.add(TIT2(text="WRONG"))
    tags.add(TPE1(text="WRONG"))
    tags.add(TXXX(desc="REPLAYGAIN_TRACK_GAIN", text="-7.0 dB"))
    tags.save(p)
    assert write_tags(p, {"title": "Ditto", "artist": "NewJeans"}) is True
    out = read_tags(p)
    assert out["title"] == "Ditto" and out["artist"] == "NewJeans"
    assert ID3(p)["TXXX:REPLAYGAIN_TRACK_GAIN"].text == ["-7.0 dB"]


def test_write_tags_skips_junk_and_empty(tmp_path):
    from mutagen.id3 import ID3
    from app.metadata.tagging import write_tags
    p = str(tmp_path / "song.mp3")
    ID3().save(p)
    assert write_tags(p, {"title": "  ", "artist": "Unknown Artist",
                          "album": "Unknown Album", "tracknumber": 0,
                          "date": "nodate"}) is False
    assert read_tags(p)["title"] is None


def test_write_tags_missing_or_unsupported():
    from app.metadata.tagging import write_tags
    assert write_tags("/nonexistent/x.mp3", {"title": "T"}) is False
    assert write_tags("/nonexistent/x.txt", {"title": "T"}) is False
    assert write_tags("/nonexistent/x.mp3", {}) is False
