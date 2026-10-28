"""`slugify`: the join key between company names, target slugs and jobs. Pure."""

from __future__ import annotations

import re
import unicodedata

_SEPARATORS = re.compile(r"[^a-z0-9]+", re.ASCII)
_APOSTROPHES = str.maketrans("", "", "'\u2019\u2018\u02bc")      # deleted, not a separator: McDonald's == McDonalds
_NO_DECOMPOSITION = {"\u00df": "ss", "\u00f8": "o", "\u0142": "l", "\u00e6": "ae", "\u0153": "oe", "\u0111": "d",
                     "\u00f0": "d", "\u00fe": "th"}   # ß ø ł æ œ đ ð þ do not decompose under NFKD
_NO_DECOMPOSITION = str.maketrans(_NO_DECOMPOSITION)


def slugify(text: object) -> str | None:
    """NFKC-normalise, lower-case, delete apostrophes, map the letters that do not decompose (ß ø ł æ œ đ ð þ) to ASCII,
    decompose (NFKD) and drop combining marks (so Café, Škoda, Zürich fold to cafe, skoda, zurich), keep ASCII letters
    and digits and join the runs between every other character with single hyphens (no leading or trailing hyphen).
    `None` when nothing is left (CJK only, symbols only, or `text` is not a string), so callers refuse instead of
    inventing a slug. Deterministic and idempotent. Limit: spellings that differ by transliteration convention stay
    different (Müller -> muller, Mueller -> mueller), so the slug is a heuristic join key."""
    if not isinstance(text, str):
        return None
    folded = unicodedata.normalize("NFKC", text).lower().translate(_APOSTROPHES).translate(_NO_DECOMPOSITION)
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", folded) if not unicodedata.category(ch).startswith("M"))
    return _SEPARATORS.sub("-", folded).strip("-") or None
