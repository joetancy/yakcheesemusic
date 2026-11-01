"""Search and download through an isolated musicdl installation."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from app.downloaders.base import DownloaderBase, SearchResult


class MusicdlDownloader(DownloaderBase):
    name = "musicdl"

    def _run(self, payload: dict) -> dict:
        python = os.environ.get("MUSICDL_PYTHON", "/opt/musicdl/bin/python")
        bridge = Path(__file__).with_name("musicdl_bridge.py")
        try:
            timeout = 60 if payload.get("action") == "search" else 180
            result = subprocess.run([python, str(bridge)], input=json.dumps(payload),
                                    text=True, capture_output=True, timeout=timeout, check=True)
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logging.getLogger("yakcheesemusic").warning("musicdl failed: %s %s", exc, (getattr(exc, "stderr", "") or "")[-2000:])
            raise RuntimeError("Musicdl search or download failed; check server logs.") from exc

    def search(self, title: str, artist: str, album: str = "", query: str | None = None) -> list[SearchResult]:
        data = self._run({"action": "search", "query": query or f"{title} {artist}".strip()})
        return [SearchResult(provider=self.name, **row) for row in data["results"]]

    def download(self, result: SearchResult, dest_dir: str) -> str:
        data = self._run({"action": "download", "id": result.provider_track_id,
                          "query": f"{result.title} {result.artist}".strip(),
                          "dest_dir": dest_dir})
        return data["path"]
