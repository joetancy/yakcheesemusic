"""Downloader abstraction (Phase 3 stub)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResult:
    provider: str
    provider_track_id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int | None = None
    quality: str | None = None
    format: str | None = None
    source_url: str | None = None
    size: int | None = None


class DownloaderBase(ABC):
    name: str = "base"

    @abstractmethod
    def search(self, title: str, artist: str, album: str = "") -> list[SearchResult]:
        raise NotImplementedError

    @abstractmethod
    def download(self, result: SearchResult, dest_dir: str) -> str:
        """Download candidate into dest_dir; return final temp file path."""
        raise NotImplementedError
