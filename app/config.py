from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "sqlite:////app/data/yakcheesemusic.db"
    music_dir: str = "/music"
    tz: str = "Asia/Singapore"
    log_level: str = "INFO"

    # App-level
    app_host: str = "0.0.0.0"
    app_port: int = 9000

    # Sync defaults
    default_sync_schedule: str = "daily_0330"
    removed_track_policy: str = "keep"  # keep | archive | delete
    replaygain_target_lufs: float = -14.0
    provider_priority: str = "qobuz,tidal,deeper,deezer,apple,migu,qq,netease,kuwo,kugou"

    # Spotify Web API (optional; without these, import falls back to the
    # embed page which returns max 100 tracks and no ISRCs)
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # matching confidence (DB settings override these at runtime)
    match_auto: float = 90.0
    match_conditional: float = 80.0
    match_review: float = 65.0

    # download format: "original" keeps source format, "mp3" transcodes
    # lossless downloads to 320kbps MP3
    preferred_format: str = "original"

    # slskd (Soulseek) download layer
    slskd_url: str = "http://slskd:5030"
    slskd_api_key: str = ""
    slskd_search_timeout_s: int = 45
    slskd_download_timeout_s: int = 1800

    # StreamRip/Qobuz fallback for tracks Soulseek can't supply.
    # Disabled automatically when email/password are unset.
    qobuz_email: str = ""
    qobuz_password: str = ""
    qobuz_quality: int = 2  # 1=320 MP3, 2=CD 16/44.1, 3=24/<=96, 4=24/>=96
    qobuz_max_per_run: int = 25

    # StreamRip/Deezer second fallback (premium arl cookie).
    deezer_arl: str = ""
    deezer_quality: int = 2  # 0=128k, 1=320k, 2=CD 16/44.1 (paid)
    deezer_max_per_run: int = 25

    # batch sync parallelism
    sync_search_workers: int = 5
    sync_download_workers: int = 3

    # physical .m3u files for players ("" = <music_dir>/_Playlists)
    playlist_dir: str = ""

    model_config = {"env_prefix": "", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
