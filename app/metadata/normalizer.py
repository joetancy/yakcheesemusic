"""Metadata normalization stub (Phase 6)."""
from __future__ import annotations


REQUIRED_TAGS = ["title", "artist", "albumartist", "album", "tracknumber",
                 "discnumber", "date", "genre", "isrc"]


def normalize_metadata(meta: dict) -> dict:
    """Preserve official Unicode; prefer download-provider structured metadata.
    Filename parsing is last resort (handled by caller)."""
    out = {k: (meta.get(k) or "").strip() if isinstance(meta.get(k), str) else meta.get(k)
           for k in REQUIRED_TAGS if k in meta}
    return out
