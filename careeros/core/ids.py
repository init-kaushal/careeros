"""Stable entity IDs: <prefix>_<10 lowercase Crockford base32 characters>."""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, lowercase, no i l o u

PREFIXES: dict[str, str] = {
    "profile": "prf",
    "experience": "exp",
    "achievement": "ach",
    "project": "prj",
    "skill": "skl",
    "education": "edu",
    "certification": "crt",
    "goal": "gol",
    "preference": "pre",
    "constraint": "con",
    "story": "sty",
    "evidence": "evd",
    "company": "cmp",
    "person": "per",
    "job": "job",
    "requirement": "req",
    "evaluation": "evl",
    "application": "app",
    "outreach": "otr",
    "followup": "flw",
    "interview": "int",
    "round": "rnd",
    "offer": "off",
    "document": "doc",
    "decision": "dec",
    "event": "evt",
    "gap": "gap",
}

_BODY = "[0-9a-hjkmnp-tv-z]{10}"


def new_id(kind: str, existing: Iterable[str] = ()) -> str:
    try:
        prefix = PREFIXES[kind]
    except KeyError:
        raise ValueError(f"unknown entity kind {kind!r}") from None
    taken = set(existing)
    for _ in range(100):
        body = "".join(_ALPHABET[b & 31] for b in secrets.token_bytes(10))
        candidate = f"{prefix}_{body}"
        if candidate not in taken:
            return candidate
    raise RuntimeError("could not generate a unique id")


def is_valid_id(kind: str, value: object) -> bool:
    prefix = PREFIXES.get(kind)
    return bool(prefix) and isinstance(value, str) and re.fullmatch(f"{prefix}_{_BODY}", value) is not None
