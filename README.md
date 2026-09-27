# yakcheesemusic

Self-hosted playlist sync: Spotify playlists are the source of truth, Soulseek (via slskd) is the download layer.

## Quickstart (Docker)

```bash
cp .env.example .env  # if present, else create .env (see Secrets below)
docker compose up -d --build
# UI: http://<server>:9000/
# Health: http://<server>:9000/health
```

Volumes:

- `./data:/app/data` — SQLite DB (`yakcheesemusic.db`)
- `/srv/media/data/media/music:/music` — music library (shared with other players)
- `slskd-downloads` — Soulseek staging area

## Secrets (`.env`, gitignored — never commit)

| Var | Used by | Notes |
| --- | --- | --- |
| `SLSKD_API_KEY` | app + slskd (primary API key) | any random 16–255 char string |
| `SLSKD_SLSK_USERNAME` / `SLSKD_SLSK_PASSWORD` | slskd | your Soulseek login |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | app | optional; without these, scans use the slower web-harvest path |

Other env (see `compose.yaml`): `DATABASE_URL`, `MUSIC_DIR`, `TZ=Asia/Singapore`, `LOG_LEVEL`, `SLSKD_URL`.

## What it does

- **Sync** — per playlist: scan the live Spotify list → Soulseek search + score every missing track → auto-download confident matches. Manual `Sync now` per playlist, plus an **automatic schedule** (`sync_interval`: `daily_HHMM` in Asia/Singapore, `hourly`, or `off`; sweeper runs every 5 min).
- **Review workflow** — low-confidence matches land on `/review` with scored candidates, paged 10 at a time. Per track: use-this / reject / undo, skip / unskip, retry, and **custom Soulseek search terms**. `Retry all` resets review + failed back to pending.
- **Tagging** — every download is stamped with playlist-derived title/artist/album (+ track no, year, ISRC); `POST /api/library/retag` backfills the whole library.
- **Library maintenance** (dashboard buttons, all with live job status + dry runs):
  - `rescan` — delete unplayable (0-byte, corrupt) files, queue re-download
  - `dedup` — delete orphan files and sidecars (`.lrc` etc.), report byte-identical groups; `_Playlists` and referenced files are never touched
  - ReplayGain sweep (−14 LUFS, always rewrites tags), lossless→MP3 convert
- **Players** — physical `.m3u` files per playlist under `/music/_Playlists`; files are stored world-readable so Navidrome/Jellyfin (uid 1000) can play root-written downloads.

## API sketch

- Playlists: `GET/POST /api/playlists`, `PATCH/DELETE /api/playlists/{id}`, `POST …/scan|sync|m3u`, `GET …/tracks`
- Tracks: `GET /api/tracks?status=`, `GET/POST …/{id}/search` (async, `{"query"}` optional), `…/download|retry|reject|unreject|skip|unskip`, `POST /api/tracks/retry-all`
- Library jobs: `POST /api/replaygain|library/convert|library/retag|library/rescan|library/dedup`, `GET /api/jobs[/{id}]`, `GET /api/stats`

## Local dev

Requires Python 3.12+ with pip:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload --port 8080
```

Tests are hermetic (temp `DATABASE_URL`/`MUSIC_DIR` via `tests/conftest.py`) — safe to run anywhere. CI (`.github/workflows/build.yml`) runs `pytest` with ffmpeg plus a Docker build on every push/PR.

## Layout

`app/providers` playlist adapters · `app/downloaders` Soulseek layer ·
`app/matching` scoring · `app/metadata` tagging · `app/audio` probe/convert/ReplayGain ·
`app/sync` scan, batch engine, scheduler, library jobs · `app/db` models ·
`app/api` REST · `app/templates` + `app/static` server-rendered UI (dark mode, mobile-first).
