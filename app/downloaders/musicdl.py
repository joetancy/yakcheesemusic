"""musicdl-backed downloader (Phase 3 stub)."""
from __future__ import annotations

from app.downloaders.base import DownloaderBase, SearchResult

PROVIDER_PRIORITY_DEFAULT = [
    "qobuz", "tidal", "deezer", "apple", "migu", "qq", "netease", "kuwo", "kugou",
]


class MusicdlDownloader(DownloaderBase):
    name = "musicdl"

    def __init__(self, provider_priority: list[str] | None = None):
        self.provider_priority = provider_priority or PROVIDER_PRIORITY_DEFAULT

    def search(self, title: str, artist: str, album: str = "") -> list[SearchResult]:
        raise NotImplementedError("musicdl search not implemented yet (Phase 3)")

    def download(self, result: SearchResult, dest_dir: str) -> str:
        raise NotImplementedError("musicdl download not implemented yet (Phase 5)")
