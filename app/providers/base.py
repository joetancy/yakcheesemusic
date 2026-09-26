"""Playlist provider interface. Full app must not depend on Spotify/YT specifics."""
from __future__ import annotations

from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class ProviderTrack:
    provider_track_id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int | None = None
    isrc: str | None = None
    position: int = 0
    year: int | None = None
    disc_number: int | None = None
    track_number: int | None = None


@dataclass
class ProviderPlaylist:
    provider: str
    provider_playlist_id: str
    name: str
    url: str
    track_count: int = 0
    tracks: list[ProviderTrack] = field(default_factory=list)
    complete: bool = True  # False when the source caps the list (e.g. embed = max 100)


class PlaylistProvider(ABC):
    provider_name: str = "base"

    @abstractmethod
    def validate_url(self, url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_playlist(self, url: str) -> ProviderPlaylist:
        raise NotImplementedError

    @abstractmethod
    def get_tracks(self, url: str) -> list[ProviderTrack]:
        raise NotImplementedError
