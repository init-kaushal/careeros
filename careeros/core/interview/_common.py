"""The small record validators the round parser needs. Pure. They mirror `outreach/_common.py` deliberately instead of
importing it: the interview package imports nothing from outreach (plan section 8, item 1)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from careeros.core.approvals import render
from careeros.core.compensation import record
from careeros.core.interview import rules
from careeros.core.models import Issue

MAX_FILE_TEXT = 1_000_000       # characters of a whole record file; a round record is a few hundred bytes
FIX = "fix the record by hand or re-run the command that wrote it (see docs/interviews.md); nothing repairs it for you"
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", re.ASCII)


def load(text: object, rel: str) -> tuple[dict | None, str, list[str]]:
    """`(frontmatter, body, [])` of `text`, or `(None, "", problems)`. Never raises."""
    if not isinstance(text, str):
        return None, "", [f"{rel} is not text"]
    if len(text) > MAX_FILE_TEXT:
        return None, "", [f"{rel} is larger than {MAX_FILE_TEXT} characters"]
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None, "", [f"{rel} contains text that cannot be encoded as UTF-8"]
    try:
        fm, body = record.strict_frontmatter(text, rel)
    except record.RecordError as exc:
        return None, "", [str(exc)]
    except Exception as exc:  # noqa: BLE001 - hostile input must never escape as an exception
        return None, "", [f"{rel} cannot be read: {type(exc).__name__}"]
    return fm, body, []


def keys_problems(fm: dict, required: tuple[str, ...], optional: tuple[str, ...] = (), where: str = "") -> list[str]:
    problems = []
    extra = sorted(str(k) for k in fm if k not in required + optional)
    if extra:
        problems.append(f"{where}unknown key(s): {', '.join(extra[:10])}{' ...' if len(extra) > 10 else ''}")
    missing = [k for k in required if k not in fm]
    if missing:
        problems.append(f"{where}missing key(s): {', '.join(missing)}")
    return problems


def schema_problem(fm: dict) -> str | None:
    value = fm.get("schema")
    return None if type(value) is int and value == rules.SCHEMA else f"schema must be {rules.SCHEMA}"


def text_value(value: object, name: str, problems: list[str], *, allow_empty: bool = False) -> str | None:
    """`value` when it is a string without control, bidi or invisible characters (they appear in terminal output),
    non-blank unless `allow_empty`, and at most `rules.MAX_TEXT` characters."""
    if not isinstance(value, str):
        problems.append(f"{name} must be a string")
        return None
    if not allow_empty and not value.strip():
        problems.append(f"{name} must not be empty")
        return None
    if len(value) > rules.MAX_TEXT:
        problems.append(f"{name} is longer than {rules.MAX_TEXT} characters")
        return None
    for char in value:
        if char != "\\" and render.needs_escape(char):
            problems.append(f"{name} contains a control, bidi or invisible character (U+{ord(char):04X})")
            return None
    return value


def _yaml_scalar(value: object) -> object:
    """A YAML date as `YYYY-MM-DD`; a YAML datetime as `YYYY-MM-DDTHH:MM:SSZ` only when its offset is zero and it has no
    fraction of a second. Anything else is returned as is.

    Documented limit: an UNQUOTED YAML timestamp is already a datetime when it gets here, so its source spelling is lost.
    Every spelling YAML reads as a zero-offset, whole-second timestamp is therefore accepted and normalised to the one
    canonical `...Z` string: `Z`, `+00:00`, `-00:00`, a space or lower-case `t` separator, a single-digit month or day
    (`2026-1-5T15:00:00Z`) and a zero fraction (`.0`). The instant is the same, so INT008 and the views stay consistent.
    A QUOTED string must be exactly `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SSZ`; naive, non-zero offset and non-zero fraction
    datetimes are refused either way."""
    if isinstance(value, datetime):
        offset = value.utcoffset()
        if offset is None or offset != timedelta(0) or value.microsecond:
            return None
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def parse_when(value: object) -> datetime | None:
    """The instant of an ISO date (its UTC midnight) or of a UTC datetime `YYYY-MM-DDTHH:MM:SSZ`, as a naive UTC datetime;
    None for anything else."""
    if not isinstance(value, str):
        return None
    try:
        if _DATE.fullmatch(value):
            return datetime.combine(date.fromisoformat(value), datetime.min.time())
        if _DATETIME.fullmatch(value):
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return None


def is_date_only(value: str) -> bool:
    return _DATE.fullmatch(value) is not None


def date_value(value: object, name: str, problems: list[str]) -> str | None:
    """An ISO `YYYY-MM-DD` date given as a YAML date or a string; a time of day is refused."""
    value = _yaml_scalar(value)
    if isinstance(value, str) and _DATE.fullmatch(value) and parse_when(value) is not None:
        return value
    problems.append(f"{name} must be an ISO date like 2026-11-05")
    return None


def when_value(value: object, name: str, problems: list[str]) -> str | None:
    """An ISO date `YYYY-MM-DD` or a UTC datetime `YYYY-MM-DDTHH:MM:SSZ`, given as a YAML scalar or a string."""
    value = _yaml_scalar(value)
    if parse_when(value) is not None:
        return value
    problems.append(f"{name} must be an ISO date like 2026-11-05 or a UTC datetime like 2026-11-05T15:00:00Z")
    return None


def issues(rel: str, problems: list[str]) -> list[Issue]:
    return [Issue("error", rules.INT001, rel, problem, FIX) for problem in problems or [f"{rel} is not a valid record"]]


def file_name_problem(rel: str, expected: str) -> str | None:
    if rel.endswith(".md") and rel.rsplit("/", 1)[-1] != expected:
        return f"the file name must be {expected}"
    return None
