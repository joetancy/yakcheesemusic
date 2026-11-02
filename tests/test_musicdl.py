import json
import subprocess

import pytest

from app.downloaders.musicdl import MusicdlDownloader
from app.sync.download import _downloader_for


def test_musicdl_search_maps_results(monkeypatch):
    def run(args, **kwargs):
        assert json.loads(kwargs["input"])["query"] == "淘汰 陳奕迅"
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {
                    "results": [
                        {
                            "provider_track_id": "NeteaseMusicClient|65528",
                            "title": "淘汰",
                            "artist": "陈奕迅",
                            "album": "",
                            "format": "flac",
                        }
                    ]
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run)
    result = MusicdlDownloader().search("淘汰", "陳奕迅")[0]
    assert result.provider == "musicdl"
    assert result.provider_track_id == "NeteaseMusicClient|65528"
    assert result.format == "flac"
    assert isinstance(_downloader_for(result.provider), MusicdlDownloader)


def test_musicdl_timeout_is_reported(monkeypatch):
    def run(args, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(args, 60)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Musicdl search or download failed"):
        MusicdlDownloader().search("title", "artist")
