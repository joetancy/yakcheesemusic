"""Matching engine: score + select best candidate (Phase 4)."""
from __future__ import annotations

from app.matching.normalization import normalize_text, has_variant_mismatch
from app.matching.scoring import score_candidate, classify_score

VARIANT_PENALTY = 25.0


def match_candidates(source: dict, candidates: list[dict],
                     auto: float = 90.0, conditional: float = 80.0,
                     review: float = 65.0) -> list[dict]:
    scored = []
    for c in candidates:
        penalty = 0.0
        combined_src = f"{source.get('title','')} {source.get('artist','')}"
        combined_cand = f"{c.get('title','')} {c.get('artist','')}"
        if has_variant_mismatch(combined_src, combined_cand):
            penalty = VARIANT_PENALTY
        score = score_candidate(
            {"title": normalize_text(source.get("title","")),
             "artist": normalize_text(source.get("artist","")),
             "album": normalize_text(source.get("album","")),
             "duration_ms": source.get("duration_ms"),
             "isrc": source.get("isrc")},
            {"title": normalize_text(c.get("title","")),
             "artist": normalize_text(c.get("artist","")),
             "album": normalize_text(c.get("album","")),
             "duration_ms": c.get("duration_ms"),
             "isrc": c.get("isrc")},
            variant_penalty=penalty,
        )
        scored.append({**c, "score": score,
                       "verdict": classify_score(score, auto, conditional, review)})
    return sorted(scored, key=lambda x: x["score"], reverse=True)
