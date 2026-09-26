"""Match scoring (Phase 4). Weights: title 35 / artist 30 / album 15 / duration 15 / ISRC 5."""
from __future__ import annotations

import difflib

TITLE_W = 0.35
ARTIST_W = 0.30
ALBUM_W = 0.15
DURATION_W = 0.15
ISRC_W = 0.05


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def duration_score(source_ms: int | None, cand_ms: int | None) -> float:
    if not source_ms or not cand_ms:
        return 0.5
    diff = abs(source_ms - cand_ms) / 1000.0
    if diff <= 2:
        return 1.0
    if diff <= 5:
        return 0.8
    if diff <= 10:
        return 0.4
    return 0.0


def score_candidate(
    source: dict, candidate: dict, variant_penalty: float = 0.0
) -> float:
    """Return 0-100 score. Dicts need title/artist/album/duration_ms/isrc keys."""
    t = similarity(source.get("title", ""), candidate.get("title", ""))
    a = similarity(source.get("artist", ""), candidate.get("artist", ""))
    al = similarity(source.get("album", ""), candidate.get("album", ""))
    d = duration_score(source.get("duration_ms"), candidate.get("duration_ms"))
    s_isrc, c_isrc = (source.get("isrc") or ""), (candidate.get("isrc") or "")
    isrc = 1.0 if (s_isrc and c_isrc and s_isrc.upper() == c_isrc.upper()) else 0.0
    score = 100 * (t * TITLE_W + a * ARTIST_W + al * ALBUM_W + d * DURATION_W + isrc * ISRC_W)
    if s_isrc and c_isrc and s_isrc.upper() == c_isrc.upper():
        score = min(100.0, score + 10.0)  # exact ISRC bonus
    return max(0.0, min(100.0, score - variant_penalty))


def classify_score(score: float, auto: float = 90.0, conditional: float = 80.0,
                   review: float = 65.0) -> str:
    if score >= auto:
        return "auto"
    if score >= conditional:
        return "auto_if_no_competition"
    if score >= review:
        return "needs_review"
    return "reject"
