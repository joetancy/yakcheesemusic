"""SQLite-backed tunable settings. DB rows override env/config defaults."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Setting

KEYS = ("match_auto", "match_conditional", "match_review", "preferred_format")


def _config_default(key: str) -> str:
    return str(getattr(get_settings(), key))


def get_setting(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    return _config_default(key)


def get_float(db: Session, key: str) -> float:
    try:
        return float(get_setting(db, key))
    except (TypeError, ValueError):
        return float(_config_default(key))


def get_thresholds(db: Session) -> tuple[float, float, float]:
    """(auto, conditional, review) match thresholds."""
    return (get_float(db, "match_auto"), get_float(db, "match_conditional"),
            get_float(db, "match_review"))


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def all_effective(db: Session) -> dict:
    return {k: get_setting(db, k) for k in KEYS}


def validate(key: str, value: str, current: dict) -> float | str:
    """Return parsed value or raise ValueError."""
    if key == "preferred_format":
        v = str(value).lower()
        if v not in ("original", "mp3"):
            raise ValueError("preferred_format must be 'original' or 'mp3'")
        return v
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    if not 0 <= v <= 100:
        raise ValueError(f"{key} must be between 0 and 100")
    auto = float(current.get("match_auto", 90))
    conditional = float(current.get("match_conditional", 80))
    review = float(current.get("match_review", 65))
    if key == "match_auto":
        auto = v
    elif key == "match_conditional":
        conditional = v
    elif key == "match_review":
        review = v
    if not 0 <= review <= conditional <= auto <= 100:
        raise ValueError("need 0 <= match_review <= match_conditional <= match_auto <= 100")
    if not review < auto:
        raise ValueError("match_review must be below match_auto")
    return v
