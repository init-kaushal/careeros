"""Shared pieces of the strict record parsers (person and target). Pure."""

from __future__ import annotations

import re
from datetime import date

from careeros.core.approvals import render
from careeros.core.compensation import record
from careeros.core.models import Issue
from careeros.core.outreach import rules
from careeros.core.workspace import normalize_value

MAX_TEXT = 1_000_000        # characters; a record is a few hundred bytes
MAX_FIELD = 500             # characters of any one text field
MAX_LIST = 50               # entries of found_in or evidence
FIX = "fix the record by hand or re-run the command that wrote it (see docs/outreach.md); nothing repairs it for you"
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)


def load(text: object, rel: str) -> tuple[dict | None, list[str]]:
    """The frontmatter mapping of `text`, or `(None, problems)`. Never raises."""
    if not isinstance(text, str):
        return None, [f"{rel} is not text"]
    if len(text) > MAX_TEXT:
        return None, [f"{rel} is larger than {MAX_TEXT} characters"]
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None, [f"{rel} contains text that cannot be encoded as UTF-8"]
    try:
        fm, _ = record.strict_frontmatter(text, rel)
    except record.RecordError as exc:
        return None, [str(exc)]
    except Exception as exc:  # noqa: BLE001 - hostile input must never escape as an exception
        return None, [f"{rel} cannot be read: {type(exc).__name__}"]
    return fm, []


def keys_problems(fm: dict, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> list[str]:
    problems = []
    extra = sorted(str(k) for k in fm if k not in required + optional)
    if extra:
        problems.append(f"unknown key(s): {', '.join(extra[:10])}{' ...' if len(extra) > 10 else ''}")
    missing = [k for k in required if k not in fm]
    if missing:
        problems.append(f"missing key(s): {', '.join(missing)}")
    return problems


def schema_problem(fm: dict) -> str | None:
    value = fm.get("schema")
    return None if type(value) is int and value == rules.SCHEMA else f"schema must be {rules.SCHEMA}"


def text_value(value: object, name: str, problems: list[str], *, allow_empty: bool = False) -> str | None:
    """`value` when it is a string without control, bidi or invisible characters (they appear in terminal output),
    non-blank unless `allow_empty`, and at most MAX_FIELD characters."""
    if not isinstance(value, str):
        problems.append(f"{name} must be a string")
        return None
    if not allow_empty and not value.strip():
        problems.append(f"{name} must not be empty")
        return None
    if len(value) > MAX_FIELD:
        problems.append(f"{name} is longer than {MAX_FIELD} characters")
        return None
    for char in value:
        if char != "\\" and render.needs_escape(char):
            problems.append(f"{name} contains a control, bidi or invisible character (U+{ord(char):04X})")
            return None
    return value


def date_value(value: object, name: str, problems: list[str]) -> str | None:
    """An ISO `YYYY-MM-DD` date given as a YAML date or a string; a time of day is refused."""
    value = normalize_value(value)
    if isinstance(value, str) and _DATE.fullmatch(value):
        try:
            date.fromisoformat(value)
            return value
        except ValueError:
            pass
    problems.append(f"{name} must be an ISO date like 2026-10-19")
    return None


def issues(rel: str, problems: list[str]) -> list[Issue]:
    return [Issue("error", rules.OUT001, rel, problem, FIX) for problem in problems or [f"{rel} is not a valid record"]]


def file_name_problem(rel: str, expected: str) -> str | None:
    if rel.endswith(".md") and rel.rsplit("/", 1)[-1] != expected:
        return f"the file name must be {expected}"
    return None
