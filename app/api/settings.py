from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import get_db
from app.db import settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings_route(db: Session = Depends(get_db)):
    s = get_settings()
    return {
        "music_dir": s.music_dir,
        "provider_priority": s.provider_priority,
        "replaygain_target_lufs": s.replaygain_target_lufs,
        "removed_track_policy": s.removed_track_policy,
        "default_sync_schedule": s.default_sync_schedule,
        "tz": s.tz,
        "match_auto": float(settings_store.get_setting(db, "match_auto")),
        "match_conditional": float(settings_store.get_setting(db, "match_conditional")),
        "match_review": float(settings_store.get_setting(db, "match_review")),
        "preferred_format": settings_store.get_setting(db, "preferred_format"),
    }


@router.patch("")
def update_settings(payload: dict, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    current = settings_store.all_effective(db)
    updated = {}
    for key in settings_store.KEYS:
        if key in payload:
            try:
                v = settings_store.validate(key, payload[key], current)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
            settings_store.set_setting(db, key, str(v))
            current[key] = str(v)
            updated[key] = v
    if not updated:
        raise HTTPException(status_code=422,
                            detail=f"No tunable settings in payload (keys: {list(settings_store.KEYS)})")
    return {"ok": True, "updated": updated,
            "bands": f"auto >= {current['match_auto']}, conditional >= {current['match_conditional']}, "
                     f"review >= {current['match_review']}, else reject"}
