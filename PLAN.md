# PLAN.md — yakcheesemusic implementation plan

Source of truth: Spotify playlists. Download layer: Soulseek via slskd.
Library: `/music` → `/srv/media/data/media/music`. TZ: Asia/Singapore.
(YouTube Music import and yt-dlp were dropped; musicdl's account-free
sources proved unusable from this server — see Soulseek decision.)

Pipeline: playlist adapters → track catalogue → matching engine →
download provider search (musicdl) → metadata normalization →
artwork + ReplayGain (-14 LUFS) → local library.

One downloaded track may belong to many playlists (`playlist_tracks`).

Provider priority (configurable): Qobuz > TIDAL > Deezer > Apple > Migu >
QQ > NetEase > Kuwo > Kugou. No YouTube audio by default.

Match weights: title 35 / artist 30 / album 15 / duration 15 / ISRC 5,
exact-ISRC bonus, variant penalties (live/remix/karaoke/… unless in source),
duration gates (≤2s excellent, ≤5s good, ≤10s weak, >10s reject unless manual).
Thresholds: ≥90 auto, 80–89 auto if no close rival, 65–79 review, <65 reject.

Quality: lossless > bit depth > sample rate > bitrate > lossy.
Removed-track policy: keep (default) / archive / delete; never delete while
another active playlist references the track.

## Phases

1. Foundation (DONE): FastAPI + SQLite + models + config + base UI +
   Dockerfile + compose + /health + tests. `docker compose up -d` serves UI.
2. Spotify import: provider + URL intake + membership + UI + tests.
3. musicdl search: abstraction + per-provider search + candidates in UI.
4. Matching engine: normalization + fuzzy + duration/album/variant/ISRC + thresholds.
5. Download pipeline: staging → validate → move → dedupe → provenance.
6. Metadata + artwork: mutagen normalize + embed + verify.
7. ReplayGain: rsgain -14 LUFS tags-only + retry + configurable target.
8. Full sync: new/removed detection + SyncJob + progress.
9. Scheduling: APScheduler from SQLite, survives restart, no host cron.
10. ~~YouTube Music provider on same engine.~~ Dropped: Spotify-only now.
11. Review UI: manual match/retry/skip, remembered decisions.
12. Quality upgrades: detect + atomic replace, never downgrade.
13. Hardening: migrations, backups, healthchecks, locking, backoff, disk checks.

MVP = Spotify → import → search → match → download → metadata → artwork →
ReplayGain -14 → daily sync + failed-match page.

See README.md for run instructions. API per spec section 22.
