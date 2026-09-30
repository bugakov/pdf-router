"""Quality predicates for the routing pipeline.

``looks_like_text`` — is there readable text on this page?
``looks_garbled``  — is the text layer present but NOT decodable?

The second question exists because a subset font without /ToUnicode (or with
a /Differences array of arbitrary subset glyph names) yields a text layer
full of codes that no font-to-Unicode rule can resolve: the page *looks*
like text to any alpha-ratio check, yet carries zero readable characters.
All functions are pure (no IO).
"""

from __future__ import annotations

import re
import string



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

# ── looks_garbled ────────────────────────────────────────────────────────
# Tuned on the real corpus, not on theory. The corpus is full of page kinds
# that LOOK like garbage to a naive ratio check and are perfectly readable:
# contents pages with dot leaders, markdown table scaffolding, figure
# captions, all-caps title pages, rows of bare numbers. Every threshold
# below exists because that case actually showed up here.

#: Markdown scaffolding added by the renderer, not by the document: table
#: borders and separators, bold/italic marks, heading hashes, code ticks,
#: guillemets, inline HTML tags. Removed before the text is judged.
_MD_NOISE = re.compile(r"[*#|`~«»]")
_MD_TAG = re.compile(r"<[^>]{0,8}>")

#: Dot leaders of a contents page — the real text is the words, the dots are
#: page furniture. Collapsed, not counted.
_DOT_LEADER = re.compile(r"[.]{3,}")

#: Characters a custom code table is built from: ASCII punctuation + digits.
#: Real text uses these sparingly; a subset-encoded layer is mostly them.
_CODE_PUNCT = frozenset(string.punctuation + string.digits)

#: A run must be at least this long to be judged — short fragments carry no
#: statistics (mirrors the short-string rule in looks_like_text).
_GARBLED_MIN_RUN = 12

#: Below this alpha share a run is code soup, not prose.
_GARBLED_MAX_ALPHA = 0.30

#: …and only when code punctuation is a real share of it, so a bare row of
#: digits (a statistical table: "1930 1931 1932") is left alone.
_GARBLED_MIN_PUNCT = 0.30

#: A run needs this many letters to be code soup. Code soup always mixes
#: letter-like codes with punctuation; pure-digit rows never do.
_GARBLED_MIN_LETTERS = 3

#: Subset-encoded Cyrillic often lands entirely in the upper code range, so
#: the alpha share alone can read as 0.8. The tell is a long unbroken token:
#: real words are 3-13 characters, a code run is not.
_GARBLED_MIN_TOKEN = 14
_GARBLED_MAX_TOKEN_ALPHA = 0.85

#: A page is only condemned when the garbage is the page's substance, not a
#: caption or a running head sitting among clean text.
_GARBLED_MIN_RUNS = 2
_GARBLED_MIN_SHARE = 0.34

#: Punctuation that separates a code run from its neighbours. Used only by
#: the mixed-token test below, never by the ratio tests — an em dash is
#: ordinary Russian typography and must not inflate a "punctuation" share.
_EDGE_PUNCT = _CODE_PUNCT | frozenset("—–…·•")

#: Edge punctuation for the mixed-token test: punctuation ONLY, never digits.
#: Stripping digits too would read "0.0KNOPQROSONTUPVVWRX" as a plain word
#: (its "0.0" looks like an edge) and miss exactly the tokens this predicate
#: exists to catch.
_PUNCT_ONLY = frozenset(string.punctuation) | frozenset("—–…·•")

#: A standalone number, optionally with a decimal comma and a "+0,15" /
#: "±0,02" error term — the shape every cell of a measurement table takes.
_NUMBER_TOKEN = re.compile(r"\A\d+(?:[.,]\d+)?(?:[+±]\d+(?:[.,]\d+)?)?\Z")

#: Author initials ("Б.Т.", "P.") — punctuation by typography, not by a code
#: table, so they never make a token mixed.
_INITIALS = re.compile(r"\A\w\.(?:\w\.?){0,2}\Z")

#: A line whose tokens are mostly numbers is a table row (a label followed by
#: measurements), not code soup. Calibrated on the corpus: measurement rows
#: sit at 0.70-0.91, code soup at 0.00.
_MAX_NUMBER_SHARE = 0.50


def _number_share(run: str) -> float:
    """Share of the run's tokens that are plain numbers."""
    tokens = [t for t in run.split() if t]
    if not tokens:
        return 0.0
    return sum(1 for t in tokens if _NUMBER_TOKEN.match(t)) / len(tokens)


def _strip_markdown(line: str) -> str:
    """Reduce a markdown line to the characters the document actually had."""
    line = _DOT_LEADER.sub(" ", _MD_NOISE.sub(" ", _MD_TAG.sub(" ", line)))
    return line.replace("__", " ").strip()


def _mixed_token(run: str) -> bool:
    """True if a token has letters and punctuation woven through it.

    This is the discriminator that separates code soup from the two page
    kinds that imitate it. A statistical row is a LABEL then NUMBERS —
    ``Салака 10,44 6,22 13,64`` — every token is either a word or a number,
    never both. Code soup is ``8637,-./,0]'##!`` and ``0.0KNOPQROSONTUPVVWRX``:
    letters wedged between punctuation inside one token. Bibliography
    entries (``Esсheriсh 1932:178—258``, ``pis. 1—34``) also stay clean —
    their punctuation only ever sits at a token's edge.
    """
    for token in run.split():
        if _INITIALS.match(token):
            continue
        core = token.strip("".join(_PUNCT_ONLY))
        if not core:
            continue
        if any(c.isalpha() for c in core) and any(c in _PUNCT_ONLY for c in core):
            return True
    return False


def _is_garbled_run(run: str) -> bool:
    """True if one line is unambiguously code soup, not readable text."""
    n = len(run)
    if n < _GARBLED_MIN_RUN:
        return False
    if _number_share(run) >= _MAX_NUMBER_SHARE:
        return False  # a measurement row: a label followed by numbers
    if not _mixed_token(run):
        return False
    letters = sum(1 for c in run if c.isalpha())
    punct = sum(1 for c in run if c in _CODE_PUNCT)
    if letters < _GARBLED_MIN_LETTERS:
        return False  # bare numbers and page furniture: a data table
    if letters / n < _GARBLED_MAX_ALPHA and punct / n >= _GARBLED_MIN_PUNCT:
        return True
    if not any(c.islower() for c in run):
        longest = max((len(w) for w in run.split()), default=0)
        if (
            longest >= _GARBLED_MIN_TOKEN
            and letters / n < _GARBLED_MAX_TOKEN_ALPHA
            and punct >= 2
        ):
            return True
    return False


def garbled_runs(s: str) -> list[str]:
    """The lines of ``s`` that read as code soup."""
    return [
        r
        for r in (_strip_markdown(ln) for ln in s.splitlines())
        if _is_garbled_run(r)
    ]


def looks_garbled(s: str) -> bool:
    """True if ``s`` is a text layer that cannot be decoded into real text.

    Page-level on purpose, and deliberately hard to trip: one odd line (a
    caption, a formula, a barcode row) is never enough, the garbage has to
    be the page's substance. Callers gate this on the document-level
    ``has_encoding_issues`` flag, so a clean book is never judged at all.
    """
    stripped = s.strip()
    if len(stripped) < _GARBLED_MIN_RUN:
        return False
    runs = garbled_runs(stripped)
    if len(runs) < _GARBLED_MIN_RUNS:
        return False
    bad = sum(len(r) for r in runs)
    return bad / len(stripped) >= _GARBLED_MIN_SHARE
