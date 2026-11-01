"""Run musicdl separately so its dependencies do not conflict with streamrip."""
import contextlib
import json
import sys
from pathlib import Path

# Avoid shadowing the installed package with the adjacent musicdl.py adapter.
sys.path.pop(0)
SOURCES = ("QQMusicClient", "NeteaseMusicClient")


def main(request):
    from musicdl import musicdl

    configs = {source: {"search_size_per_source": 10, "max_retries": 1,
                        "work_dir": request.get("dest_dir", "/tmp/musicdl-search")}
               for source in SOURCES}
    client = musicdl.MusicClient(music_sources=list(SOURCES), init_music_clients_cfg=configs)
    results = client.search(keyword=request["query"])
    songs = [song for source in SOURCES for song in results.get(source, [])
             if song.identifier and song.song_name and song.singers]
    if request["action"] == "search":
        return {"results": [{"provider_track_id": f"{song.source}|{song.identifier}",
                             "title": song.song_name, "artist": song.singers,
                             "album": song.album or "",
                             "duration_ms": int(song.duration_s * 1000) if song.duration_s else None,
                             "quality": song.ext, "format": song.ext,
                             "size": song.file_size_bytes,
                             "source_url": f"musicdl://{song.source}/{song.identifier}"}
                            for song in songs]}
    if request["action"] != "download":
        raise ValueError("Unknown musicdl operation")
    song = next((song for song in songs if f"{song.source}|{song.identifier}" == request["id"]), None)
    if not song:
        raise RuntimeError("The selected musicdl result is no longer available. Search again.")
    downloaded = client.download(song_infos=[song])
    if not downloaded or not downloaded[0]._save_path:
        raise RuntimeError("Musicdl did not download the selected track.")
    path = Path(downloaded[0]._save_path).resolve()
    if not path.is_relative_to(Path(request["dest_dir"]).resolve()) or not path.is_file() or not path.stat().st_size:
        raise RuntimeError("Musicdl did not produce a valid file in the download directory.")
    return {"path": str(path)}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    with contextlib.redirect_stdout(sys.stderr):
        response = main(request)
    print(json.dumps(response, ensure_ascii=False))
