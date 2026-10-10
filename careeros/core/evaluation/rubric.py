"""Fit rows from the `## Scoring rubric` table of `profile.md` (spec 4.3). Pure; never raises on bad input.

Only the first table under the heading, above the fold, is read. Anything ambiguous degrades rather than guesses:
an unknown weight word, an empty label or a duplicate label drops that row and flags the rubric `invalid`, in which
case the fit score is omitted entirely (an ambiguous rubric must not be half-scored). Row identity, used by the agent's
`fit[].row`, is the normalised label (`norm_label`).

`table_under` is shared with `markets.py`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from careeros.core.validation import _above_fold

WEIGHTS = {"high": 3, "medium": 2, "low": 1}
_HEADING = re.compile(r"^#{1,6}\s")
_SEPARATOR = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")
_PIPE = re.compile(r"(?<!\\)\|")
_RUBRIC_HEADING = re.compile(r"^##\s+scoring rubric\b", re.I)


def norm_label(label: str) -> str:
    """NFC, whitespace collapsed, case folded: the identity of a rubric row or market."""
    return unicodedata.normalize("NFC", " ".join(unicodedata.normalize("NFC", label).split()).casefold())


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.replace("\\|", "|").strip() for cell in _PIPE.split(body)]


def table_under(text: str, heading: re.Pattern) -> list[list[str]] | None:
    """The cells of the first markdown table under the first heading matching `heading`, above the fold.

    Returns `[header, row, row, ...]` or None when the heading or a table with a separator row is missing. The
    section ends at the next heading of any level.
    """
    lines = _above_fold(text).splitlines()
    start = next((i for i, line in enumerate(lines) if heading.match(line.strip())), None)
    if start is None:
        return None
    block: list[str] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if _HEADING.match(stripped):
            break
        if stripped.startswith("|"):
            block.append(stripped)
        elif block:
            break
    if len(block) < 2 or not _SEPARATOR.match(block[1]):
        return None
    return [_cells(block[0]), *(_cells(line) for line in block[2:])]


@dataclass(frozen=True)
class FitRow:
    label: str
    weight: int

    @property
    def key(self) -> str:
        return norm_label(self.label)


@dataclass(frozen=True)
class Rubric:
    """`state` is `ok`, `missing` (no table) or `invalid` (a row was dropped as ambiguous)."""

    rows: tuple[FitRow, ...]
    problems: tuple[str, ...]
    state: str

    @property
    def usable(self) -> bool:
        """True when fit may be scored: a well-formed rubric with at least one fit row."""
        return self.state == "ok" and bool(self.rows)


def parse(profile_text: str) -> Rubric:
    table = table_under(profile_text, _RUBRIC_HEADING)
    if table is None:
        return Rubric((), ("rubric missing",), "missing")
    header = [norm_label(c) for c in table[0]]
    weight_at = header.index("weight") if "weight" in header else 1
    rows: list[FitRow] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, cells in enumerate(table[1:], start=1):
        label = cells[0] if cells else ""
        weight_cell = cells[weight_at] if weight_at < len(cells) else ""
        if not label:
            problems.append(f"row {number}: empty label dropped")
            continue
        lowered = label.casefold()
        if "compensation" in lowered or "sponsorship" in lowered or norm_label(weight_cell).startswith("negative"):
            continue
        weight = WEIGHTS.get(norm_label(weight_cell))
        if weight is None:
            problems.append(f"row {number} ({label}): unknown weight {weight_cell!r} dropped")
            continue
        key = norm_label(label)
        if key in seen:
            problems.append(f"row {number} ({label}): duplicate label dropped")
            continue
        seen.add(key)
        rows.append(FitRow(label, weight))
    return Rubric(tuple(rows), tuple(problems), "invalid" if problems else "ok")
