"""SQLAlchemy models for music-sync. See PLAN.md section 5."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(32), default="spotify")  # spotify only
    provider_playlist_id: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_interval: Mapped[str] = mapped_column(String(64), default="daily_0330")
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    memberships: Mapped[list["PlaylistTrack"]] = relationship(back_populates="playlist", cascade="all, delete-orphan")
    jobs: Mapped[list["SyncJob"]] = relationship(back_populates="playlist", cascade="all, delete-orphan")


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    artist: Mapped[str] = mapped_column(String(512), default="")
    album: Mapped[str] = mapped_column(String(512), default="")
    album_artist: Mapped[str] = mapped_column(String(512), default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    isrc: Mapped[str | None] = mapped_column(String(32), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disc_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bitrate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bit_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    download_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending|searching|matched|downloading|processing|completed|needs_review|failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    memberships: Mapped[list["PlaylistTrack"]] = relationship(back_populates="track", cascade="all, delete-orphan")
    candidates: Mapped[list["DownloadCandidate"]] = relationship(back_populates="track", cascade="all, delete-orphan")

    @property
    def quality_label(self) -> str | None:
        fmt = (self.format or "").lower()
        if fmt in ("flac", "alac", "wav", "wv", "ape"):
            if self.bit_depth and self.sample_rate:
                return f"{fmt.upper()} {self.bit_depth}/{self.sample_rate / 1000:g}"
            return fmt.upper() or None
        if self.bitrate:
            return f"{fmt.upper() + ' ' if fmt else ''}{self.bitrate}"
        for c in self.candidates or []:  # fall back to chosen candidate label
            if c.selected and c.quality:
                return c.quality
        if fmt:
            return fmt.upper()
        return None


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    __table_args__ = (UniqueConstraint("playlist_id", "provider_track_id", name="uq_playlist_provider_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"))
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True)
    provider_track_id: Mapped[str] = mapped_column(String(255), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    source_title: Mapped[str] = mapped_column(String(512), default="")
    source_artist: Mapped[str] = mapped_column(String(512), default="")
    source_album: Mapped[str] = mapped_column(String(512), default="")
    source_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    playlist: Mapped[Playlist] = relationship(back_populates="memberships")
    track: Mapped[Track | None] = relationship(back_populates="memberships")


class DownloadCandidate(Base):
    __tablename__ = "download_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(64), default="")
    provider_track_id: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    artist: Mapped[str] = mapped_column(String(512), default="")
    album: Mapped[str] = mapped_column(String(512), default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    track: Mapped[Track] = relationship(back_populates="candidates")


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[int | None] = mapped_column(ForeignKey("playlists.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|success|failed
    tracks_seen: Mapped[int] = mapped_column(Integer, default=0)
    tracks_added: Mapped[int] = mapped_column(Integer, default=0)
    tracks_removed: Mapped[int] = mapped_column(Integer, default=0)
    tracks_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    tracks_failed: Mapped[int] = mapped_column(Integer, default=0)
    tracks_upgraded: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    playlist: Mapped[Playlist | None] = relationship(back_populates="jobs")


class Setting(Base):
    """User-tunable settings (override env defaults, survive restarts)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
