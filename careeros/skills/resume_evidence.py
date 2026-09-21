from __future__ import annotations

# A quote longer than this is rejected as evidence even when it genuinely
# occurs. An unbounded quote verifies trivially — hand back the whole
# resume and every skill "checks out". Evidence that cites everything
# cites nothing.
MAX_QUOTE_CHARS = 200

# Characters that read as word-internal even though str.isalnum() says no.
# Without this, "C" matches inside "C++", "R" inside "R&D", "Go" inside
# "go-to-market", and "Go" inside "go_to_market_dashboard" — plausible skill
# names a model will actually propose, each one a fabricated citation if
# allowed through. The Unicode dashes are here because a resume exported from
# a word processor has them in place of the ASCII hyphen.
_WORD_INTERNAL = set("-+#&_\u2010\u2011\u2012\u2013\u2014")


def _normalize(text: str) -> str:
    """Collapse whitespace and fold case. Deliberately nothing else.

    No fuzzy matching, no partial matching, no stemming. The normalization
    that makes matching forgiving is exactly what lets a fabricated quote
    through, and here a false match is an invented skill wearing a citation
    indistinguishable from a real one.
    """
    return " ".join(text.lower().split())


def _find_bounded(needle: str, haystack: str) -> int | None:
    """Index of `needle` in `haystack`, but only where it is not embedded
    inside a longer word.

    Plain containment matches "Go" inside "Golang" and "Java" inside
    "JavaScript" — characters that occur in the resume but are not evidence
    of the skill being claimed. A boundary-aligned match is.
    """
    start = haystack.find(needle)
    while start != -1:
        before_ok = start == 0 or (
            not haystack[start - 1].isalnum() and haystack[start - 1] not in _WORD_INTERNAL
        )
        end = start + len(needle)
        after_ok = end == len(haystack) or (
            not haystack[end].isalnum() and haystack[end] not in _WORD_INTERNAL
        )
        if before_ok and after_ok:
            return start
        start = haystack.find(needle, start + 1)
    return None


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
    if not any(ch.isalnum() for ch in needle):
        # A quote with no alphanumeric content (e.g. a lone "-") matches
        # almost anywhere and evidences nothing.
        return None

    lines = source_text.split("\n")
    for index, line in enumerate(lines, start=1):
        if _find_bounded(needle, _normalize(line)) is not None:
            return index

    # The quote may span a line break in the source; fall back to locating
    # it in the normalized whole and reporting the line the match starts on.
    # This assumes the per-line word counts sum to the same tokenization as
    # the whole-document split — a future change to _normalize or the line
    # splitting can silently break the line attribution.
    haystack = _normalize(source_text)
    needle_pos = _find_bounded(needle, haystack)
    if needle_pos is None:
        return None
    prefix_words = len(haystack[:needle_pos].split())
    seen = 0
    for index, line in enumerate(lines, start=1):
        words = len(_normalize(line).split())
        if seen + words > prefix_words:
            return index
        seen += words
    return None
