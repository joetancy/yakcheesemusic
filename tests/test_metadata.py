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
