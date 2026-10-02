"""Public audio search and downloads from the Internet Archive."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import httpx

from app.downloaders.base import DownloaderBase, SearchResult

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/{identifier}"
DOWNLOAD_URL = "https://archive.org/download/{identifier}/{filename}"
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".wma"}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ").replace("-", " ")).strip()


def _file_title(filename: str) -> str:
    stem = Path(filename).stem
    # Common uploaded album naming: "01 - Track Name" or "01 Track Name".
    return _clean(re.sub(r"^\s*\d{1,3}[\s._-]+", "", stem)) or stem


def _duration_ms(value) -> int | None:
    try:
        if not value:
            return None
        if isinstance(value, str) and ":" in value:
            parts = [float(part) for part in value.split(":")]
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + part
            return int(seconds * 1000)
        return int(float(value) * 1000)
    except (TypeError, ValueError):
        return None


def _first(value):
    return value[0] if isinstance(value, list) and value else value


class InternetArchiveDownloader(DownloaderBase):
    """Search item metadata and download an openly accessible audio file."""

    name = "internet_archive"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def search(self, title: str, artist: str, album: str = "") -> list[SearchResult]:
        # Solr query quoting: escape reserved characters inside phrase values.
        def phrase(s: str) -> str:
            return '"' + re.sub(r'([+\-&|!(){}\[\]^~*?:\\])', r"\\\1", s.strip()) + '"'

        query = f'mediatype:audio AND title:{phrase(title)}'
        if artist:
            query += f' AND creator:{phrase(artist)}'
        with httpx.Client(timeout=self.timeout, follow_redirects=True,
                          headers={"User-Agent": "YakCheeseMusic/1.0 (audio search)"}) as client:
            response = client.get(SEARCH_URL, params={
                "q": query,
                "fl[]": ["identifier", "title", "creator", "date", "downloads"],
                "rows": 10,
                "page": 1,
                "output": "json",
            })
            response.raise_for_status()
            docs = response.json().get("response", {}).get("docs", [])
            results: list[SearchResult] = []
            for doc in docs:
                identifier = doc.get("identifier")
                if not identifier:
                    continue
                metadata_response = client.get(METADATA_URL.format(identifier=quote(str(identifier), safe="")))
                metadata_response.raise_for_status()
                metadata = metadata_response.json()
                item = metadata.get("metadata") or {}
                if str(item.get("nodownload", "false")).lower() == "true":
                    continue
                files = metadata.get("files") or []
                item_title = str(item.get("title") or doc.get("title") or album or "")
                item_artist = _first(item.get("creator") or doc.get("creator") or artist or "")
                item_album = _first(item.get("album") or item_title)
                audio_files = [f for f in files
                               if Path(f.get("name", "")).suffix.lower() in AUDIO_EXTS
                               and str(f.get("private", "false")).lower() != "true"]
                for file in audio_files:
                    name = file.get("name", "")
                    ext = Path(name).suffix.lower()
                    file_title = _file_title(name)
                    # A single-file item often has a useful title in its metadata;
                    # multi-file albums are represented by their individual filenames.
                    if len(audio_files) == 1:
                        file_title = str(item.get("title") or doc.get("title") or file_title)
                    length = file.get("length")
                    try:
                        size = int(file.get("size")) if file.get("size") else None
                    except (TypeError, ValueError):
                        size = None
                    key = f"{identifier}|{name}"
                    results.append(SearchResult(
                        provider=self.name, provider_track_id=key,
                        title=file_title or item_title, artist=str(item_artist or ""),
                        album=str(item_album or ""), duration_ms=_duration_ms(length),
                        quality="Internet Archive", format=ext.lstrip("."),
                        source_url=f"https://archive.org/details/{quote(str(identifier), safe='')}",
                        size=size,
                    ))
                    if len(results) >= 30:
                        return results
            return results

    def download(self, result: SearchResult, dest_dir: str) -> str:
        try:
            identifier, filename = result.provider_track_id.split("|", 1)
        except ValueError as exc:
            raise ValueError("invalid Internet Archive result id") from exc
        url = DOWNLOAD_URL.format(identifier=quote(identifier, safe=""),
                                  filename=quote(filename, safe="/"))
        target_dir = Path(dest_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / Path(filename).name
        partial = target.with_name(target.name + ".part")
        try:
            with httpx.stream("GET", url, timeout=120.0, follow_redirects=True,
                              headers={"User-Agent": "YakCheeseMusic/1.0"}) as response:
                response.raise_for_status()
                with partial.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            partial.replace(target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return str(target)
