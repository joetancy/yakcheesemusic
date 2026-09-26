"""Automatic playlist syncs: a 5-minute sweeper launches due playlists.

Schedule spec lives on Playlist.sync_interval:
  "daily_HHMM" (e.g. daily_0330, Asia/Singapore), "hourly", or
  "off"/"disabled"/"manual" for manual-only. Unknown specs are skipped
  with a warning, never crash the sweeper.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger("yakcheesemusic")

SGT = ZoneInfo("Asia/Singapore")
UTC = timezone.utc

scheduler = BackgroundScheduler(timezone="Asia/Singapore")


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()
    try:
        scheduler.remove_job("due_syncs")
    except Exception:
        pass
    scheduler.add_job(check_due_syncs, "interval", minutes=5, id="due_syncs",
                      next_run_time=datetime.now(UTC) + timedelta(minutes=1),
                      max_instances=1, coalesce=True)


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def parse_interval(spec: str | None) -> tuple | None:
    """"daily_0330" -> ("daily", 3, 30); "hourly" -> ("hourly",);
    "off"/"disabled"/"manual"/"" -> ("off",). None on garbage."""
    s = (spec or "").strip().lower()
    if s in ("", "off", "disabled", "manual", "never"):
        return ("off",)
    if s == "hourly":
        return ("hourly",)
    import re
    m = re.fullmatch(r"daily_(\d{2})(\d{2})", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return ("daily", hh, mm)
    return None


def next_occurrence(spec: str | None, now_utc: datetime | None = None) -> datetime | None:
    """Next run after now, naive UTC (matches codebase timestamps). None if off."""
    parsed = parse_interval(spec)
    if not parsed or parsed[0] == "off":
        return None
    now_utc = (now_utc or datetime.now(UTC)).replace(tzinfo=UTC)
    now_sgt = now_utc.astimezone(SGT)
    if parsed[0] == "hourly":
        cand = now_sgt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        _, hh, mm = parsed
        cand = now_sgt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= now_sgt:
            cand += timedelta(days=1)
    return cand.astimezone(UTC).replace(tzinfo=None)


def check_due_syncs(session_factory=None) -> list[int]:
    """Launch syncs for due playlists. Returns launched playlist ids."""
    from sqlalchemy import select
    from app.db.database import SessionLocal
    from app.db.models import Playlist, SyncJob
    from app.sync.batch import run_playlist_sync
    import threading

    session_factory = session_factory or SessionLocal
    now = datetime.now(UTC).replace(tzinfo=None)
    launched = []
    db = session_factory()
    try:
        playlists = db.execute(select(Playlist).where(
            Playlist.enabled.is_(True), Playlist.sync_enabled.is_(True))).scalars().all()
        for p in playlists:
            nxt = next_occurrence(p.sync_interval, now.replace(tzinfo=UTC))
            if nxt is None:
                continue  # manual-only or garbage spec
            if p.next_sync_at is None:
                # adopt the schedule without an immediate run
                p.next_sync_at = nxt
                db.commit()
                continue
            if p.next_sync_at > now:
                continue
            running = db.execute(select(SyncJob).where(
                SyncJob.playlist_id == p.id, SyncJob.status == "running",
                SyncJob.finished_at.is_(None))).scalars().first()
            if running:
                log.info("playlist=%s action=schedule status=skip running job=%s",
                         p.id, running.id)
                p.next_sync_at = nxt  # don't pile up missed runs
                db.commit()
                continue
            job = SyncJob(playlist_id=p.id, status="running")
            db.add(job)
            db.commit()
            db.refresh(job)
            p.next_sync_at = nxt
            db.commit()
            threading.Thread(target=run_playlist_sync,
                             args=(session_factory, p.id, job.id), daemon=True).start()
            log.info("playlist=%s action=schedule status=launched job=%s", p.id, job.id)
            launched.append(p.id)
    except Exception:
        log.exception("action=schedule status=error")
    finally:
        db.close()
    return launched
