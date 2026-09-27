"""Isolated TestClient: API tests run against in-memory SQLite, never the dev DB."""
import os
import tempfile

# Isolate BEFORE any app import: the lifespan + module engine + music dir must
# never touch production paths (this also makes CI work without /app or /music).
_test_tmp = tempfile.mkdtemp(prefix="yakcheesemusic-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_test_tmp}/test.db"
os.environ["MUSIC_DIR"] = os.path.join(_test_tmp, "music")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.main import app


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
