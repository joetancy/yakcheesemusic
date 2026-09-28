"""Qobuz fallback downloader via StreamRip (library use, no CLI).

Role: tracks Soulseek can't supply. Disabled automatically when
QOBUZ_EMAIL/QOBUZ_PASSWORD are unset (__init__ raises, callers skip).

Auth: email + md5(password); streamrip bootstraps app_id/secrets on first
login and persists them to our config file, so the file is generated once
and then only our fields are refreshed.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os

from app.config import get_settings
from app.downloaders.base import DownloaderBase, SearchResult

log = logging.getLogger("yakcheesemusic")

CONFIG_PATH = "/app/data/streamrip.toml"
STAGING_DIR = "/downloads/qobuz"

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav"}


def configured() -> bool:
    s = get_settings()
    return bool(s.qobuz_email and s.qobuz_password)


def ensure_config() -> str | None:
    """(Re)write our streamrip config from env, preserving streamrip's own
    fields (app_id/secrets) across regenerations. None when unconfigured."""
    s = get_settings()
    if not configured():
        return None
    app_id, secrets = "", []
    if os.path.exists(CONFIG_PATH):
        try:
            import tomllib
            with open(CONFIG_PATH, "rb") as f:
                q = tomllib.load(f).get("qobuz", {})
            app_id = q.get("app_id") or ""
            secrets = q.get("secrets") or []
        except Exception as e:
            log.warning("could not read %s: %s", CONFIG_PATH, e)
    import json
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    body = f"""[downloads]
folder = "{STAGING_DIR}"
source_subdirectories = false
disc_subdirectories = false
concurrency = false
max_connections = 2
requests_per_minute = 60
verify_ssl = true

[qobuz]
quality = {int(s.qobuz_quality or 2)}
download_booklets = false
use_auth_token = false
email_or_userid = "{s.qobuz_email}"
password_or_token = "{hashlib.md5(s.qobuz_password.encode()).hexdigest()}"
app_id = "{app_id}"
secrets = {json.dumps(list(secrets))}
"""
    with open(CONFIG_PATH, "w") as f:
        f.write(body)
    os.makedirs(STAGING_DIR, exist_ok=True)
    return CONFIG_PATH


def _track_to_result(item: dict) -> SearchResult | None:
    """Defensive mapping of a Qobuz track search hit."""
    if not isinstance(item, dict):
        return None
    tid = item.get("id")
    if tid is None:
        return None
    title = item.get("title") or item.get("name") or "Unknown Title"
    perf = item.get("performer") or item.get("artist") or {}
    artist = (perf.get("name") if isinstance(perf, dict) else str(perf)) or ""
    alb = item.get("album") or {}
    album = (alb.get("title") if isinstance(alb, dict) else str(alb)) or ""
    dur = item.get("duration") or item.get("duration_seconds")
    try:
        duration_ms = int(dur * 1000) if dur else None
    except (TypeError, ValueError):
        duration_ms = None
    return SearchResult(
        provider="qobuz",
        provider_track_id=f"qobuz:{tid}",
        title=str(title), artist=str(artist), album=str(album),
        duration_ms=duration_ms,
        quality="Qobuz", format=None,
        source_url=item.get("url") or (f"https://www.qobuz.com/track/{tid}"),
    )


class QobuzDownloader(DownloaderBase):
    name = "qobuz"

    def __init__(self, quality: int | None = None):
        s = get_settings()
        if not configured():
            raise RuntimeError("Qobuz not configured (QOBUZ_EMAIL/QOBUZ_PASSWORD)")
        self.quality = quality or int(s.qobuz_quality or 2)
        self._config_path = ensure_config()

    def _new_client(self):
        from streamrip.config import Config
        from streamrip.client.qobuz import QobuzClient
        return QobuzClient(Config(self._config_path))

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
            log.warning("qobuz search failed (%s)", e)
            return []
        out = []
        for item in items or []:
            r = _track_to_result(item)
            if r:
                out.append(r)
        return out

    async def _adownload(self, track_id: str, dest_dir: str) -> str:
        from streamrip.config import Config
        from streamrip.client.qobuz import QobuzClient
        os.makedirs(dest_dir, exist_ok=True)
        client = QobuzClient(Config(self._config_path))
        await client.login()
        try:
            dl = await client.get_downloadable(track_id, self.quality)
            tmp = os.path.join(dest_dir, f"qobuz-{track_id}")
            await dl.download(tmp, lambda _n: None)
        finally:
            try:
                await client.session.close()
            except Exception:
                pass
        return self._locate(dest_dir)

    @staticmethod
    def _locate(dest_dir: str) -> str:
        """Find the downloaded audio file (streamrip names it, not us)."""
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
            raise FileNotFoundError(f"qobuz download produced no audio in {dest_dir}")
        if time.time() - best_mtime > 3600:
            log.warning("qobuz download picked stale file %s", best)
        return best

    def download(self, result: SearchResult, dest_dir: str) -> str:
        tid = (result.provider_track_id or "").split(":", 1)[-1]
        if not tid:
            raise RuntimeError("bad qobuz track id")
        try:
            return asyncio.run(asyncio.wait_for(
                self._adownload(tid, dest_dir), timeout=600))
        except Exception as e:
            raise RuntimeError(f"qobuz download failed: {e}")
