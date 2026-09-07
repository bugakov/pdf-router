"""Quality predicates for the routing pipeline.

``looks_like_text`` is the only predicate in v0.3 routing.
All functions are pure (no IO).
"""

from __future__ import annotations

import re


def looks_like_text(
    s: str,
    *,
    language: str | None = None,
    threshold: float = 0.30,
) -> bool:
    """Return True if ``s`` looks like valid text.

    Short strings (< 8 chars) are always trusted — not enough data to judge.

    When ``language`` is set, only characters from that language's Unicode range
    count toward the alpha ratio. When ``language`` is None, any alphabetic
    character counts.

    Args:
        s: extracted text string to evaluate.
        language: expected language hint, e.g. "ru" for Cyrillic. None = any.
        threshold: minimum fraction of alphabetic characters required (default 0.30).
            Calibrated against the project corpus: 0 false negatives at every
            threshold from 0.10 to 0.60. See TKT-RT-11.
    """
    if not s.strip():
        return True  # empty — not our job to judge
    if len(s.strip()) < 8:
        return True  # too short to accumulate statistics

    stripped = s.strip()

    if language == "ru":
        # Count Cyrillic characters only (U+0400..U+04FF).
        cyrillic = sum(1 for c in stripped if "\u0400" <= c <= "\u04FF")
        return (cyrillic / max(len(stripped), 1)) > threshold

    # Language-agnostic: any alphabetic character counts.
    letters = sum(1 for c in stripped if c.isalpha())
    return (letters / max(len(stripped), 1)) > threshold


# Cyrillic Unicode range used in looks_like_text — exported for tests.
CYRILLIC_RANGE = ("\u0400", "\u04FF")
