"""Text normalization for matching (Phase 4)."""
from __future__ import annotations

import re
import unicodedata

_VARIANT_TERMS = [
    "live", "remix", "karaoke", "instrumental", "sped up", "slowed",
    "nightcore", "acoustic", "cover", "demo", "radio edit", "extended mix",
]


def normalize_text(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKC", s)
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def strip_variant_terms(s: str) -> str:
    """Remove parenthetical/bracketed variant hints for base comparison."""
    base = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", s or "")
    return normalize_text(base)


def detect_variants(s: str) -> list[str]:
    low = normalize_text(s)
    return [v for v in _VARIANT_TERMS if v in low]


def has_variant_mismatch(source: str, candidate: str) -> bool:
    """True if candidate contains a variant term absent from the source."""
    src = set(detect_variants(source))
    cand = set(detect_variants(candidate))
    return bool(cand - src)
