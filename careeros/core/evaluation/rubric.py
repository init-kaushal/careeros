"""Fit rows from the `## Scoring rubric` table of `profile.md` (spec 4.3). Pure; never raises on bad input.

Only the first table under the heading, above the fold, is read. Anything ambiguous degrades rather than guesses:
an unknown weight word, an empty label or a duplicate label drops that row and flags the rubric `invalid`, in which
case the fit score is omitted entirely (an ambiguous rubric must not be half-scored). The label is the first column that is not the Weight column (found by header; no Weight column means invalid). Row identity, used by the agent's
`fit[].row`, is the normalised label (`norm_label`).

`table_under` is shared with `markets.py`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from careeros.core.fold import above_fold

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


def _visible_lines(text: str) -> list[str]:
    """Lines above the fold with fenced code blocks and HTML comments blanked (so they end nothing, define nothing)."""
    out: list[str] = []
    fence = comment = False
    for line in above_fold(text).splitlines():
        stripped = line.strip()
        if comment:
            comment = "-->" not in stripped
            out.append("<!--")          # marks a skipped line inside a table
            continue
        if stripped.startswith(("```", "~~~")):
            fence = not fence
            out.append("")
            continue
        if fence:
            out.append("")
            continue
        if stripped.startswith("<!--"):
            comment = "-->" not in stripped
            out.append("<!--")
            continue
        out.append(line)
    return out


def table_under(text: str, heading: re.Pattern) -> tuple[list[list[str]] | None, int]:
    """The first markdown table under the first heading matching `heading`, above the fold, and the heading count.

    Returns (`[header, row, row, ...]` or None when the heading or a table with a separator row is missing, number
    of matching headings). Fenced code blocks are ignored, single- and multi-line HTML comments inside the table are
    skipped, and the section ends at the next heading of any level.
    """
    lines = _visible_lines(text)
    starts = [i for i, line in enumerate(lines) if heading.match(line.strip())]
    if not starts:
        return None, 0
    block: list[str] = []
    for line in lines[starts[0] + 1:]:
        stripped = line.strip()
        if stripped == "<!--":
            continue
        if _HEADING.match(stripped):
            break
        if stripped.startswith("|"):
            block.append(stripped)
        elif block:
            break
    if len(block) < 2 or not _SEPARATOR.match(block[1]):
        return None, len(starts)
    return [_cells(block[0]), *(_cells(line) for line in block[2:])], len(starts)


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
    table, headings = table_under(profile_text, _RUBRIC_HEADING)
    if table is None:
        return Rubric((), ("rubric missing",), "missing")
    problems: list[str] = []
    if headings > 1:
        problems.append("more than one Scoring rubric heading")
    header = [norm_label(c) for c in table[0]]
    if "weight" not in header:
        return Rubric((), (*problems, "rubric table has no Weight column"), "invalid")
    weight_at = header.index("weight")
    label_at = next(i for i in range(len(header)) if i != weight_at)  if len(header) > 1 else weight_at
    rows: list[FitRow] = []
    seen: set[str] = set()
    for number, cells in enumerate(table[1:], start=1):
        label = cells[label_at] if label_at < len(cells) else ""
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
