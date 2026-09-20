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

# Punctuation-bearing technology tokens that _PUNCT would otherwise strip
# down to a bare letter, colliding two different roles into one: "C++
# Engineer" and "C# Engineer" both became "c engineer", and ".NET Developer"
# collided with a literal "NET Developer". Replaced with alphanumeric
# equivalents BEFORE the punctuation strip, on word boundaries, so a lone
# "c" (as in "C Engineer") is left untouched.
_TECH_TOKENS = (
    (re.compile(r"(?<![a-z0-9])c\+\+(?![a-z0-9])"), "cplusplus"),
    (re.compile(r"(?<![a-z0-9])c#(?![a-z0-9])"), "csharp"),
    (re.compile(r"(?<![a-z0-9])f#(?![a-z0-9])"), "fsharp"),
    (re.compile(r"(?<![a-z0-9])\.net(?![a-z0-9])"), "dotnet"),
)


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
    lowered = (title or "").lower()
    for pattern, replacement in _TECH_TOKENS:
        lowered = pattern.sub(replacement, lowered)
    cleaned = _PUNCT.sub(" ", lowered).strip()
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

    a_title = normalize_title(candidate.title)
    b_title = normalize_title(existing.title)
    a_company = _normalize_company(candidate.company)
    b_company = _normalize_company(existing.company)

    # An empty normalization is absence of evidence, not evidence of identity.
    # Without this, two postings with punctuation-only or encoding-mangled
    # titles would match each other — a false positive, which here means a
    # real job is silently never applied to.
    if not a_title or not b_title or not a_company or not b_company:
        return False

    if a_company != b_company:
        return False
    if a_title != b_title:
        return False

    a_loc = normalize_location(candidate.location)
    b_loc = normalize_location(existing.location)
    if a_loc is None or b_loc is None:
        return True
    return a_loc == b_loc
