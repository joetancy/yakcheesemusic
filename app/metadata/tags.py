"""Read embedded tags from audio files (mutagen, read-only)."""
from __future__ import annotations


def _first(tags, *keys) -> str | None:
    for k in keys:
        try:
            v = tags.get(k)
        except Exception:
            continue
        if not v:
            continue
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _track_no(s: str | None) -> int | None:
    if not s:
        return None
    try:
        n = int(str(s).split("/")[0].strip())
        return n if 0 < n < 1000 else None
    except (ValueError, TypeError):
        return None


def _year(s: str | None) -> int | None:
    if not s:
        return None
    import re
    m = re.search(r"(19|20)\d{2}", str(s))
    return int(m.group(0)) if m else None


def read_tags(path: str) -> dict:
    """Return {artist, album, title, track_number, year} (missing keys = None)."""
    from mutagen import File as MFile
    out: dict = {"artist": None, "album": None, "title": None,
                 "track_number": None, "year": None}
    try:
        audio = MFile(path)
    except Exception:
        audio = None
    tags = getattr(audio, "tags", None) if audio is not None else None
    if not tags:
        try:  # tag-only files (e.g. fresh ID3) have no parseable audio stream
            from mutagen.id3 import ID3
            tags = ID3(path)
        except Exception:
            return out
    if not tags:
        return out
    out["artist"] = _first(tags, "TPE1", "artist", "\xa9ART", "ARTIST")
    out["album"] = _first(tags, "TALB", "album", "\xa9alb", "ALBUM")
    out["title"] = _first(tags, "TIT2", "title", "\xa9nam", "TITLE")
    raw_no = _first(tags, "TRCK", "tracknumber", "trkn", "TRACKNUMBER")
    if raw_no and "," in str(raw_no):  # mp4 trkn tuple
        raw_no = str(raw_no).strip("()").split(",")[0]
    out["track_number"] = _track_no(raw_no)
    out["year"] = _year(_first(tags, "TDRC", "TYER", "date", "\xa9day", "DATE"))
    return out
