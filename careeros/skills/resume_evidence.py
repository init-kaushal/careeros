from __future__ import annotations

# A quote longer than this is rejected as evidence even when it genuinely
# occurs. An unbounded quote verifies trivially — hand back the whole
# resume and every skill "checks out". Evidence that cites everything
# cites nothing.
MAX_QUOTE_CHARS = 200


def _normalize(text: str) -> str:
    """Collapse whitespace and fold case. Deliberately nothing else.

    No fuzzy matching, no partial matching, no stemming. The normalization
    that makes matching forgiving is exactly what lets a fabricated quote
    through, and here a false match is an invented skill wearing a citation
    indistinguishable from a real one.
    """
    return " ".join(text.lower().split())


def verify_quote(quote: str, source_text: str) -> int | None:
    """Return the 1-indexed line where `quote` occurs, or None.

    Plain containment, never a regex: resumes are full of `C++`, `.NET`
    and `(2018–present)`, which as patterns either raise or match wrongly.
    """
    if not quote or not quote.strip():
        return None
    if len(quote) > MAX_QUOTE_CHARS:
        return None

    needle = _normalize(quote)
    if not needle:
        return None

    lines = source_text.split("\n")
    for index, line in enumerate(lines, start=1):
        if needle in _normalize(line):
            return index

    # The quote may span a line break in the source; fall back to locating
    # it in the normalized whole and reporting the line the match starts on.
    haystack = _normalize(source_text)
    if needle not in haystack:
        return None
    prefix_words = len(haystack[: haystack.index(needle)].split())
    seen = 0
    for index, line in enumerate(lines, start=1):
        words = len(_normalize(line).split())
        if seen + words > prefix_words:
            return index
        seen += words
    return None
