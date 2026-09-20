from __future__ import annotations

import re
import urllib.parse

from careeros.core.models import Job

# Tracking parameters safe to drop. A DENYLIST, not "strip everything":
# some ATS links carry the job id in a query parameter, and blanket-stripping
# would collapse every posting on such a board into a single record.
_TRACKING_PARAMS = ("gh_src", "trk", "refid", "originalsubdomain")

_ABBREVIATIONS = {
    "sr": "senior",
    "jr": "junior",
    "eng": "engineer",
    "sre": "site reliability engineer",
    "swe": "software engineer",
    "mgr": "manager",
    "dev": "developer",
}

_PUNCT = re.compile(r"[^a-z0-9]+")


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in _TRACKING_PARAMS


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not _is_tracking(k)]
    )
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, query, "")
    )


def normalize_title(title: str) -> str:
    cleaned = _PUNCT.sub(" ", (title or "").lower()).strip()
    # Whole-token expansion only. A substring replace would turn
    # "engineering" into "engineerineering".
    return " ".join(_ABBREVIATIONS.get(tok, tok) for tok in cleaned.split())


def normalize_location(loc: str | None) -> str | None:
    if not loc or not loc.strip():
        return None
    cleaned = _PUNCT.sub(" ", loc.lower()).strip()
    return " ".join(cleaned.split()) or None


def _normalize_company(company: str) -> str:
    return " ".join(_PUNCT.sub(" ", (company or "").lower()).split())


def is_same_posting(candidate: Job, existing: Job) -> bool:
    """Identity: same canonical URL, else same company + title + location.

    Location is a wildcard when absent on either side, because boards word it
    inconsistently. Two differently-worded non-empty locations do NOT match —
    an accepted limitation that errs toward a visible duplicate rather than
    silently collapsing two distinct roles into one.
    """
    a_url = canonical_url(candidate.url)
    if a_url and a_url == canonical_url(existing.url):
        return True

    if _normalize_company(candidate.company) != _normalize_company(existing.company):
        return False
    if normalize_title(candidate.title) != normalize_title(existing.title):
        return False

    a_loc = normalize_location(candidate.location)
    b_loc = normalize_location(existing.location)
    if a_loc is None or b_loc is None:
        return True
    return a_loc == b_loc
