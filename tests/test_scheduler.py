from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Playlist, SyncJob
from app.sync.scheduler import parse_interval, next_occurrence, check_due_syncs


def test_parse_interval():
    assert parse_interval("daily_0330") == ("daily", 3, 30)
    assert parse_interval("DAILY_0000") == ("daily", 0, 0)
    assert parse_interval("hourly") == ("hourly",)
    assert parse_interval("off") == ("off",)
    assert parse_interval("") == ("off",)
    assert parse_interval("daily_2460") is None
    assert parse_interval("weekly") is None


def test_next_occurrence():
    utc = timezone.utc
    # 2026-09-27 00:00 UTC == 08:00 SGT; 03:30 SGT passed -> next day
    now = datetime(2026, 9, 27, 0, 0, tzinfo=utc)
    assert next_occurrence("daily_0330", now) == datetime(2026, 9, 27, 19, 30)
    # 2026-09-26 18:00 UTC == 02:00 SGT; 03:30 SGT still ahead today
    now2 = datetime(2026, 9, 26, 18, 0, tzinfo=utc)
    assert next_occurrence("daily_0330", now2) == datetime(2026, 9, 26, 19, 30)
    assert next_occurrence("hourly", now) == datetime(2026, 9, 27, 1, 0)
    assert next_occurrence("off", now) is None
    assert next_occurrence("garbage", now) is None


def _mem_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)


def test_sweeper_launches_due_and_adopts_schedule(monkeypatch):
    import app.sync.batch as batch
    calls = []
    monkeypatch.setattr(batch, "run_playlist_sync",
                        lambda sf, pid, jid: calls.append((pid, jid)))

    factory = _mem_factory()
    db = factory()
    past = datetime(2020, 1, 1)
    db.add(Playlist(name="Due", provider="spotify", url="u1",
                    sync_enabled=True, sync_interval="daily_0330",
                    next_sync_at=past))
    db.add(Playlist(name="Manual", provider="spotify", url="u2",
                    sync_enabled=False, sync_interval="daily_0330",
                    next_sync_at=past))
    db.add(Playlist(name="Fresh", provider="spotify", url="u3",
                    sync_enabled=True, sync_interval="daily_0330",
                    next_sync_at=None))
    db.add(Playlist(name="Off", provider="spotify", url="u4",
                    sync_enabled=True, sync_interval="off",
                    next_sync_at=past))
    db.commit()
    db.close()

    launched = check_due_syncs(factory)

    assert launched == [1]
    assert calls and calls[0][0] == 1
    db = factory()
    due, manual, fresh, off = (db.get(Playlist, i) for i in (1, 2, 3, 4))
    assert due.next_sync_at > datetime.now(timezone.utc).replace(tzinfo=None)
    assert manual.next_sync_at == past  # disabled untouched
    assert fresh.next_sync_at is not None and fresh.next_sync_at > past  # adopted
    assert off.next_sync_at == past  # manual-only untouched
    assert db.query(SyncJob).filter_by(playlist_id=1).count() == 1
    db.close()
