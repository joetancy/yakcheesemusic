"""Soulseek downloader via slskd HTTP API (no paid accounts needed).

Flow: start search -> poll until complete/timeout -> collect audio files ->
caller scores them -> enqueue download -> poll transfer -> locate file.
"""
from __future__ import annotations

import time
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.downloaders.base import DownloaderBase, SearchResult

import logging

log = logging.getLogger("yakcheesemusic")

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wv", ".ape", ".alac"}


def _ext_of(filename: str) -> str:
    name = (filename or "").replace("\\", "/").split("/")[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def describe_quality(f: dict) -> tuple[str | None, str | None]:
    """(quality label, format) from an slskd file entry."""
    ext = _ext_of(f.get("filename", "")).lstrip(".") or None
    br, bd, sr = f.get("bitRate"), f.get("bitDepth"), f.get("sampleRate")
    if ext == "flac" and bd and sr:
        quality = f"FLAC {bd}/{sr / 1000:g}"
    elif ext == "flac":
        quality = "FLAC"
    elif br and ext in ("mp3", "aac", "ogg", "opus", "m4a"):
        quality = f"{ext.upper()} {br}"
    elif br:
        quality = str(br)
    else:
        quality = ext.upper() if ext else None
    return quality, ext


def parse_filename(filename: str) -> tuple[str, str]:
    """Best-effort (artist, title) from a Soulseek remote path."""
    import re
    base = (filename or "").replace("\\", "/").split("/")[-1]
    base = base[: base.rfind(".")] if "." in base else base
    base = re.sub(r"^\d+[\s.\-_]+", "", base).strip()
    if " - " in base:
        artist, _, title = base.partition(" - ")
        return artist.strip(), title.strip()
    return "", base


def file_to_result(username: str, f: dict) -> SearchResult | None:
    if _ext_of(f.get("filename", "")) not in AUDIO_EXTS:
        return None
    quality, fmt = describe_quality(f)
    size = f.get("size") or 0
    artist, title = parse_filename(f.get("filename", ""))
    return SearchResult(
        provider="soulseek",
        provider_track_id=f"{username}/{f.get('filename')}",
        title=title or "Unknown Title", artist=artist, album="",
        duration_ms=int(f.get("length", 0) * 1000) if f.get("length") else None,
        quality=quality, format=fmt,
        source_url=f"soulseek://{username}/{f.get('filename')}",
        size=size or None,
    )


class SlskdDownloader(DownloaderBase):
    name = "soulseek"

    def __init__(self, url: str | None = None, api_key: str | None = None,
                 search_timeout_s: int | None = None):
        s = get_settings()
        self.base = (url or s.slskd_url).rstrip("/")
        self.key = api_key if api_key is not None else s.slskd_api_key
        self.search_timeout = search_timeout_s or s.slskd_search_timeout_s
        if not self.key:
            raise RuntimeError("SLSKD_API_KEY not configured")

    def _headers(self) -> dict:
        return {"X-API-Key": self.key}

    def _call(self, client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
        """Single retry layer: ride out 429/5xx with backoff (honors Retry-After)."""
        delay = 2.0
        last = None
        for _ in range(4):
            r = getattr(client, method)(url, headers=self._headers(), **kw)
            if r.status_code not in (429, 502, 503, 504):
                return r
            last = r
            try:
                wait = float(r.headers.get("Retry-After", delay))
            except (TypeError, ValueError):
                wait = delay
            wait = min(wait, 30.0)
            log.warning("slskd %s %s -> retry in %.0fs", method.upper(), url, wait)
            time.sleep(wait)
            delay *= 2
        return last

    def _check(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise RuntimeError("slskd rejected API key (401/403)")
        r.raise_for_status()

    def search(self, title: str, artist: str, album: str = "",
               query: str | None = None) -> list[SearchResult]:
        if query and query.strip():
            queries = [query.strip()]
        else:
            queries = [f"{artist} {title}".strip(), (title or "").strip(),
                       (artist or "").strip()]
            queries = [q for q in dict.fromkeys(queries) if q]
        if not queries:
            return []
        with httpx.Client(timeout=20) as c:
            for query in queries:
                out = self._run_query(c, query, title, artist, album)
                if out:
                    return out
            return []

    def _run_query(self, c: httpx.Client, query: str, title: str,
                   artist: str, album: str) -> list[SearchResult]:
        r = self._call(c, "post", f"{self.base}/api/v0/searches",
                       json={"searchText": query})
        self._check(r)
        sid = r.json().get("id")
        if not sid:
            raise RuntimeError(f"slskd search returned no id: {r.text[:200]}")
        deadline = time.time() + self.search_timeout
        while time.time() < deadline:
            s = self._call(c, "get", f"{self.base}/api/v0/searches/{sid}")
            self._check(s)
            state = (s.json().get("state") or "").lower()
            if state in ("completed", "cancelled", "timedout"):
                break
            time.sleep(2)
        resp = self._call(c, "get", f"{self.base}/api/v0/searches/{sid}/responses")
        self._check(resp)
        out: list[SearchResult] = []
        for user in resp.json() or []:
            username = user.get("username", "")
            for f in user.get("files") or []:
                sr = file_to_result(username, f)
                if sr:
                    out.append(sr)
        self._call(c, "delete", f"{self.base}/api/v0/searches/{sid}")
        return out

    def download(self, result: SearchResult, dest_dir: str) -> str:
        """Enqueue in slskd and block until the file completes. Returns final path."""
        from app.config import get_settings as _gs
        timeout_s = _gs().slskd_download_timeout_s
        username, _, filename = result.provider_track_id.partition("/")
        size = result.size
        with httpx.Client(timeout=30) as c:
            r = self._call(c, "post",
                           f"{self.base}/api/v0/transfers/downloads/{quote(username, safe='')}",
                           json=[{"filename": filename, "size": size}])
            if r.status_code == 400:
                raise RuntimeError(f"slskd rejected download: {r.text[:200]}")
            self._check(r)
            deadline = time.time() + timeout_s
            base_name = filename.replace("\\", "/").split("/")[-1].lower()
            while time.time() < deadline:
                d = self._call(c, "get", f"{self.base}/api/v0/transfers/downloads")
                self._check(d)
                found = self._walk_downloads(d.json(), base_name)
                state = (found or {}).get("state", "").lower()
                if found and ("completed" in state or "succeeded" in state):
                    return self._locate(base_name, dest_dir)
                if found and any(s in state for s in
                                 ("errored", "cancelled", "failed", "rejected")):
                    raise RuntimeError(f"soulseek transfer {found.get('state')}: {base_name}")
                time.sleep(5)
            raise TimeoutError(f"soulseek download timed out: {base_name}")

    @staticmethod
    def _walk_downloads(payload, base_name: str) -> dict | None:
        """Find our file in slskd's nested transfer listing."""
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                fn = node.get("filename", "")
                if fn and fn.replace("\\", "/").split("/")[-1].lower() == base_name \
                        and "state" in node:
                    return node
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return None

    @staticmethod
    def _locate(base_name: str, downloads_dir: str) -> str:
        import os
        for root, _, files in os.walk(downloads_dir):
            for fn in files:
                if fn.lower() == base_name:
                    return os.path.join(root, fn)
        raise FileNotFoundError(f"completed download not found under {downloads_dir}: {base_name}")
