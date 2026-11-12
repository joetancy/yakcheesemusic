"""YouTube search and MP3 extraction through yt-dlp.

Use only for videos the user is authorized to download. Channel signals are
shown as hints; they are not a guarantee of rights or source authenticity.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from pathlib import Path

from app.downloaders.base import DownloaderBase, SearchResult

log = logging.getLogger("yakcheesemusic")


def _source_label(info: dict) -> str:
    channel = (info.get("channel") or info.get("uploader") or "").strip()
    title = (info.get("title") or "").lower()
    if channel.lower().endswith(" - topic"):
        return "YouTube Music · Topic channel"
    if info.get("channel_is_verified"):
        return "YouTube · verified channel"
    if "official" in title and any(x in title for x in ("audio", "video", "music")):
        return "YouTube · labeled official (unverified)"
    return "YouTube · unverified channel"


def _clean_title(title: str, artist: str) -> str:
    value = (title or "").strip()
    if artist and value.casefold().startswith(artist.casefold() + " - "):
        value = value[len(artist) + 3 :]
    value = re.sub(r"\s*\((?:official\s+)?(?:audio|video|lyric video|lyrics|visualizer)[^)]*\)", "", value, flags=re.I)
    value = re.sub(r"\s*\[(?:official\s+)?(?:audio|video|lyrics|visualizer)[^]]*\]", "", value, flags=re.I)
    return value.strip(" -") or title or "Unknown Title"


class YouTubeDownloader(DownloaderBase):
    name = "youtube"

    @staticmethod
    def _ydl(options: dict):
        try:
            import yt_dlp
        except ImportError as exc:
            raise RuntimeError("yt-dlp is not installed") from exc
        options.setdefault("js_runtimes", {"node": {}})
        return yt_dlp.YoutubeDL(options)

    def search(
        self, title: str, artist: str, album: str = "", query: str | None = None
    ) -> list[SearchResult]:
        terms = query or " ".join(x for x in (artist, title, "official audio") if x)
        if not terms.strip():
            return []
        with self._ydl(
            {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": "in_playlist",
                "ignoreerrors": True,
                "socket_timeout": 20,
            }
        ) as ydl:
            data = ydl.extract_info(f"ytsearch10:{terms}", download=False)
        entries = (data or {}).get("entries") or []
        results = []
        seen = set()
        for item in entries:
            if not item:
                continue
            video_id = item.get("id")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            channel = (item.get("channel") or item.get("uploader") or "").strip()
            candidate_artist = re.sub(r"\s+- Topic$", "", channel, flags=re.I)
            raw_title = item.get("title") or "Unknown Title"
            cleaned = _clean_title(raw_title, artist)
            # Treat a matching artist in the video title as useful metadata;
            # don't assign the requested artist to unrelated channels.
            if artist and artist.casefold() in raw_title.casefold():
                candidate_artist = artist
            duration = item.get("duration")
            results.append(
                SearchResult(
                    provider=self.name,
                    provider_track_id=str(video_id),
                    title=cleaned,
                    artist=candidate_artist or "Unknown Artist",
                    album=item.get("album") or "",
                    duration_ms=int(duration * 1000) if duration else None,
                    quality=_source_label(item),
                    format="mp3",
                    source_url=item.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
                )
            )
        # Give likely official music uploads precedence in review results.
        def source_rank(result: SearchResult) -> int:
            label = result.quality or ""
            if "Topic channel" in label:
                return 0
            if "verified channel" in label:
                return 1
            if "labeled official" in label:
                return 2
            return 3

        results.sort(key=source_rank)
        return results[:10]

    def download(self, result: SearchResult, dest_dir: str) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        template = os.path.join(dest_dir, "youtube-%(id)s.%(ext)s")
        with self._ydl(
            {
                "format": "bestaudio/best",
                "outtmpl": template,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": 30,
                "retries": 2,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "0",
                    }
                ],
            }
        ) as ydl:
            ydl.download([result.source_url or f"https://www.youtube.com/watch?v={result.provider_track_id}"])
        matches = glob.glob(os.path.join(dest_dir, f"youtube-{result.provider_track_id}*.mp3"))
        if not matches or not Path(matches[0]).is_file():
            raise FileNotFoundError("yt-dlp completed without producing an MP3")
        return matches[0]
