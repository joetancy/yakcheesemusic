from app.matching.normalization import normalize_text, detect_variants, has_variant_mismatch
from app.matching.scoring import similarity, duration_score, score_candidate, classify_score
from app.matching.matcher import match_candidates


def test_normalize():
    assert normalize_text("  IU  -  Love Wins All ") == "iu - love wins all"


def test_variant_detection():
    assert "live" in detect_variants("Song (Live)")
    assert has_variant_mismatch("Song", "Song (Live)")
    assert not has_variant_mismatch("Song (Live)", "Song (Live Version)")


def test_duration_score():
    assert duration_score(200_000, 201_000) == 1.0
    assert duration_score(200_000, 204_000) == 0.8
    assert duration_score(200_000, 209_000) == 0.4
    assert duration_score(200_000, 220_000) == 0.0


def test_score_prefers_exact():
    src = {"title": "Love Wins All", "artist": "IU", "album": "The Winning", "duration_ms": 210000, "isrc": None}
    good = {"title": "Love Wins All", "artist": "IU", "album": "The Winning", "duration_ms": 210500, "isrc": None}
    bad = {"title": "Love Wins All (Live)", "artist": "IU Tribute", "album": "Karaoke", "duration_ms": 230000, "isrc": None}
    ranked = match_candidates(src, [bad, good])
    assert ranked[0]["title"] == "Love Wins All"
    assert ranked[0]["verdict"] in ("auto", "auto_if_no_competition")


def test_isrc_bonus():
    src = {"title": "a", "artist": "b", "album": "c", "duration_ms": 200000, "isrc": "USRC12345678"}
    cand = {"title": "a", "artist": "b", "album": "c", "duration_ms": 200000, "isrc": "usrc12345678"}
    assert score_candidate(src, cand) > 90
