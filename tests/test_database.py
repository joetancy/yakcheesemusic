import os

from app.db.database import _ensure_sqlite_dir


def test_ensure_sqlite_dir_creates_nested(tmp_path):
    target = tmp_path / "a" / "b" / "x.db"
    _ensure_sqlite_dir(f"sqlite:///{target}")
    assert os.path.isdir(tmp_path / "a" / "b")


def test_ensure_sqlite_dir_skips_memory_and_other():
    _ensure_sqlite_dir("sqlite:///:memory:")
    _ensure_sqlite_dir("sqlite://")
    _ensure_sqlite_dir("postgresql://u@h/db")  # no crash, nothing to do
