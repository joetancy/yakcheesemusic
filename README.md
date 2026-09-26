# yakcheesemusic

Self-hosted playlist sync: Spotify playlists are the source of truth, Soulseek (via slskd) is the download layer.

## Quickstart (Docker)

```bash
docker compose up -d --build
# UI: http://<server>:9000/
# Health: http://<server>:9000/health
```

Volumes:

- `./data:/app/data` — SQLite DB (`yakcheesemusic.db`)
- `/srv/media/data/media/music:/music` — music library

Env (see `compose.yaml`):

- `DATABASE_URL=sqlite:////app/data/yakcheesemusic.db`
- `MUSIC_DIR=/music`
- `TZ=Asia/Singapore`
- `LOG_LEVEL=INFO`

## Local dev

Requires Python 3.12+ with pip:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload --port 8080
```

## Status

Phase 1 (foundation) implemented: FastAPI + SQLite + models + base UI + `/health`.
Phases 2+ are stubbed per `PLAN.md` — see module docstrings for next steps.

## Layout

`app/providers` playlist adapters · `app/downloaders` musicdl layer ·
`app/matching` scoring · `app/metadata` tagging/artwork · `app/audio` ReplayGain/quality ·
`app/sync` engine + scheduler · `app/db` models · `app/api` REST.
