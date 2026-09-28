"""Deezer fallback downloader via StreamRip (library use, no CLI).

Second fallback after Qobuz. Auth is a premium account `arl` cookie.
Disabled automatically when DEEZER_ARL is unset.
"""
from __future__ import annotations

import asyncio
import logging
import os

from app.config import get_settings
from app.downloaders.base import DownloaderBase, SearchResult

log = logging.getLogger("yakcheesemusic")

CONFIG_PATH = "/app/data/streamrip.toml"
STAGING_DIR = "/downloads/deezer"

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav"}


def configured() -> bool:
    return bool(get_settings().deezer_arl)


def ensure_config() -> str | None:
    """Make sure the shared streamrip config carries our Deezer arl.
    Preserves everything else in the file (Qobuz creds, app_id...)."""
    s = get_settings()
    if not configured():
        return None
    import tomllib
    data: dict = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            log.warning("could not read %s: %s", CONFIG_PATH, e)
    qobuz = data.get("qobuz", {})
    downloads = data.get("downloads", {})
    import json

    def _q(key, default=""):
        v = qobuz.get(key, default)
        return json.dumps(v) if isinstance(v, list) else f'"{v}"'

    body = f"""[downloads]
folder = "{downloads.get("folder", STAGING_DIR)}"
source_subdirectories = false
disc_subdirectories = false
concurrency = false
max_connections = 2
requests_per_minute = 60
verify_ssl = true

[qobuz]
quality = {qobuz.get("quality", 2)}
download_booklets = false
use_auth_token = false
email_or_userid = "{qobuz.get("email_or_userid", "")}"
password_or_token = "{qobuz.get("password_or_token", "")}"
app_id = "{qobuz.get("app_id", "")}"
secrets = {_q("secrets", [])}

[deezer]
quality = {int(s.deezer_quality or 2)}
arl = "{s.deezer_arl}"
use_deezloader = true
deezloader_warnings = false
"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        f.write(body)
    os.makedirs(STAGING_DIR, exist_ok=True)
    return CONFIG_PATH


def _pick(value, *keys):
    for k in keys:
        if isinstance(value, dict) and value.get(k):
            return value[k]
    return None


def _track_to_result(item: dict) -> SearchResult | None:
    """Defensive mapping of a Deezer track search hit."""
    if not isinstance(item, dict):
        return None
    tid = _pick(item, "id", "ID", "track_id", "SNG_ID")
    if tid is None:
        return None
    title = _pick(item, "title", "TITLE", "name", "SNG_TITLE") or "Unknown Title"
    artist = _pick(item, "artist", "ARTIST") or ""
    if isinstance(artist, dict):
        artist = artist.get("name", "")
    album = _pick(item, "album", "ALBUM") or ""
    if isinstance(album, dict):
        album = album.get("title", "")
    dur = _pick(item, "duration", "DURATION", "duration_seconds")
    duration_ms = None
    try:
        dur = float(dur) if dur else None
        if dur:
            # Deezer reports seconds; guard milli-second payloads
            duration_ms = int(dur) if dur > 10000 else int(dur * 1000)
    except (TypeError, ValueError):
        duration_ms = None
    return SearchResult(
        provider="deezer",
        provider_track_id=f"deezer:{tid}",
        title=str(title), artist=str(artist), album=str(album),
        duration_ms=duration_ms,
        quality="Deezer", format=None,
        source_url=f"https://www.deezer.com/track/{tid}",
    )


class DeezerDownloader(DownloaderBase):
    name = "deezer"

    def __init__(self, quality: int | None = None):
        s = get_settings()
        if not configured():
            raise RuntimeError("Deezer not configured (DEEZER_ARL)")
        self.quality = quality or int(s.deezer_quality or 2)
        self._config_path = ensure_config()

    def _new_client(self):
        from streamrip.config import Config
        from streamrip.client.deezer import DeezerClient
        return DeezerClient(Config(self._config_path))

    async def _asearch(self, query: str, limit: int = 10) -> list[dict]:
        client = self._new_client()
        await client.login()
        try:
            return await client.search("track", query, limit=limit)
        finally:
            try:
                await client.session.close()
            except Exception:
                pass

    def search(self, title: str, artist: str, album: str = "",
               query: str | None = None) -> list[SearchResult]:
        q = query.strip() if query and query.strip() else f"{artist} {title}".strip()
        if not q:
            return []
        try:
            items = asyncio.run(self._asearch(q))
        except Exception as e:
            log.warning("deezer search failed (%s)", e)
            return []
        out = []
        for item in items or []:
            r = _track_to_result(item)
            if r:
                out.append(r)
        return out

    async def _adownload(self, track_id: str, dest_dir: str) -> str:
        from streamrip.config import Config
        from streamrip.client.deezer import DeezerClient
        os.makedirs(dest_dir, exist_ok=True)
        client = DeezerClient(Config(self._config_path))
        await client.login()
        try:
            dl = await client.get_downloadable(track_id, self.quality)
            tmp = os.path.join(dest_dir, f"deezer-{track_id}")
            await dl.download(tmp, lambda _n: None)
        finally:
            try:
                await client.session.close()
            except Exception:
                pass
        return self._locate(dest_dir)

    @staticmethod
    def _locate(dest_dir: str) -> str:
        import glob
        import time
        best, best_mtime = None, 0.0
        for p in glob.glob(os.path.join(dest_dir, "*")):
            if os.path.splitext(p)[1].lower() not in AUDIO_EXTS:
                continue
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if mt > best_mtime:
                best, best_mtime = p, mt
        if not best:
            raise FileNotFoundError(f"deezer download produced no audio in {dest_dir}")
        if time.time() - best_mtime > 3600:
            log.warning("deezer download picked stale file %s", best)
        return best

    def download(self, result: SearchResult, dest_dir: str) -> str:
        tid = (result.provider_track_id or "").split(":", 1)[-1]
        if not tid:
            raise RuntimeError("bad deezer track id")
        try:
            return asyncio.run(asyncio.wait_for(
                self._adownload(tid, dest_dir), timeout=600))
        except Exception as e:
            raise RuntimeError(f"deezer download failed: {e}")
