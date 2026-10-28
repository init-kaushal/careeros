"""`slugify`: the join key between company names, target slugs and jobs. Pure."""

from __future__ import annotations

import re
import unicodedata

_SEPARATORS = re.compile(r"[^a-z0-9]+", re.ASCII)


def slugify(text: object) -> str | None:
    """NFKC-normalise, lower-case, keep ASCII letters and digits and join the runs between other characters with single
    hyphens (no leading or trailing hyphen). `None` when nothing is left (or `text` is not a string), so callers refuse
    instead of inventing a slug. A non-ASCII letter is a separator, not transliterated. Deterministic and idempotent."""
    if not isinstance(text, str):
        return None
    folded = unicodedata.normalize("NFKC", text).lower()
    return _SEPARATORS.sub("-", folded).strip("-") or None
