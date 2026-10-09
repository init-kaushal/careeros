"""Framework and schema version handling."""

from __future__ import annotations

import re

import careeros

SCHEMA_VERSION = 1

_NUMERIC = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def installed_version() -> str:
    return careeros.__version__


def parse_version(value: str) -> tuple[int, int, int]:
    match = _NUMERIC.match(str(value))
    if not match:
        raise ValueError(f"invalid version {value!r}: expected MAJOR.MINOR.PATCH")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def compare_versions(a: str, b: str) -> int:
    pa, pb = parse_version(a), parse_version(b)
    return (pa > pb) - (pa < pb)
