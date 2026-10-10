"""The `## Markets` table of `profile.md`, parsed conservatively (spec 16.1). Pure; never raises on bad input.

The table keeps its free-text format. A floor is read only from a cell holding exactly one numeric amount and exactly
one currency indicator; a sponsorship need only from the exact text `required` or `not needed`. Everything else is
unknown, and nothing is inferred from any other field. `verify_market` compares the agent's values with the row; a
disagreement is a `conflict` and is never resolved towards the favourable value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from careeros.core.evaluation.model import JobFacts, MarketBasis, Verification
from careeros.core.evaluation.rubric import norm_label, table_under
from careeros.core.memory.normalize import scan
from careeros.core.workspace import WorkspaceError

_HEADING = re.compile(r"^##\s+markets\s*$", re.I)
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_CODES = frozenset(
    "INR USD SGD EUR GBP AUD CAD NZD CHF JPY CNY HKD AED SAR QAR SEK NOK DKK PLN CZK HUF ILS KRW THB MYR IDR PHP "
    "VND TWD BRL MXN ZAR TRY RUB NGN KES EGP PKR BDT LKR NPR".split())
_CODE = re.compile(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])")
_DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PERIOD = re.compile(r"\b(?:month(?:ly)?|mo|week(?:ly)?|wk|hour(?:ly)?|hr|day|daily|quarter(?:ly)?)\b", re.I)
_NEEDS = {"required": "yes", "not needed": "no"}


class MarketError(WorkspaceError):
    """`job_facts.market` does not name exactly one row of the `## Markets` table (EVL006)."""

    def __init__(self, message: str, code: str = "EVL006") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Floor:
    amount: int
    currency: str
    basis: MarketBasis = MarketBasis.BASE     # the column is a base figure


@dataclass(frozen=True)
class MarketRow:
    label: str
    floor_cell: str
    sponsorship_cell: str
    floor: Floor | None
    need: str | None          # "yes" | "no" | None


@dataclass(frozen=True)
class Markets:
    rows: dict[str, MarketRow]          # keyed by norm_label(label)
    problems: tuple[str, ...]
    ambiguous: frozenset[str] = frozenset()   # labels that occur more than once: unusable


def parse_floor(cell: str) -> Floor | None:
    """The floor a cell establishes, or None unless it holds exactly one amount and one currency indicator."""
    symbols = [c for c in cell if c in _SYMBOLS]
    codes = [c for c in _CODE.findall(cell) if c in _CODES]
    tokens = list(_DIGITS.finditer(cell))
    if len(symbols) + len(codes) != 1 or len(tokens) != 1 or _PERIOD.search(cell) or re.search(r"/\s?(?:mo|m|wk|w|d|h)\b", cell, re.I):
        return None
    if symbols and re.search(r"[A-Za-z][" + re.escape(symbols[0]) + "]", cell):
        return None                         # S$, A$, US$: the symbol is ambiguous
    currency = _SYMBOLS[symbols[0]] if symbols else codes[0]
    found = scan(cell)
    atoms = [*found.numbers, *found.years]
    if found.durations or len(atoms) > 1:
        return None
    if atoms:
        atom = atoms[0]
        if hasattr(atom, "unit"):
            if atom.plus or not (atom.unit == "count" or atom.unit.startswith("currency:")):
                return None
            value = atom.value
        else:
            value = float(atom.year)        # a bare 1950-2100 reads as a year to the scanner; here it is an amount
    else:
        token = tokens[0]                   # the scanner skips 7+ digit runs (phone numbers); accept a plain one
        after = cell[token.end():token.end() + 1]
        if not token.group(0).isdigit() or len(token.group(0)) < 7 or after.isalnum():
            return None
        value = float(token.group(0))
    amount = round(value)
    if amount <= 0 or abs(value - amount) > 1e-6:
        return None
    return Floor(amount, currency)


def parse_need(cell: str) -> str | None:
    """`yes` for exactly `required`, `no` for exactly `not needed` (case-insensitive); otherwise unknown."""
    return _NEEDS.get(" ".join(cell.split()).casefold())


def parse(profile_text: str) -> Markets:
    table = table_under(profile_text, _HEADING)
    if table is None:
        return Markets({}, ("markets missing",), frozenset())
    header = [norm_label(c) for c in table[0]]
    label_at = next((i for i, h in enumerate(header) if "market" in h), 0)
    floor_at = next((i for i, h in enumerate(header) if "min base" in h), None)
    need_at = next((i for i, h in enumerate(header) if "sponsorship" in h), None)
    problems: list[str] = []
    if floor_at is None:
        problems.append("markets table has no 'Min base' column")
    if need_at is None:
        problems.append("markets table has no 'Sponsorship' column")

    def cell(cells: list[str], at: int | None) -> str:
        return cells[at] if at is not None and at < len(cells) else ""

    rows: dict[str, MarketRow] = {}
    ambiguous: set[str] = set()
    for number, cells in enumerate(table[1:], start=1):
        label = cell(cells, label_at)
        if not label:
            problems.append(f"row {number}: empty market label ignored")
            continue
        key = norm_label(label)
        if key in rows or key in ambiguous:
            rows.pop(key, None)
            ambiguous.add(key)
            problems.append(f"row {number} ({label}): duplicate market label, unusable")
            continue
        floor_cell, need_cell = cell(cells, floor_at), cell(cells, need_at)
        rows[key] = MarketRow(label, floor_cell, need_cell, parse_floor(floor_cell), parse_need(need_cell))
    return Markets(rows, tuple(problems), frozenset(ambiguous))


@dataclass(frozen=True)
class FieldCheck:
    """`value` is the row's established value when `verified` (a `Floor`, or `yes`/`no`), else None; `source` is the
    exact cell text (None only when no row was named)."""

    state: Verification
    source: str | None
    value: Floor | str | None = None


@dataclass(frozen=True)
class MarketCheck:
    row: MarketRow | None
    floor: FieldCheck
    need: FieldCheck


def _agent_need(value: object) -> str | None:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, str) and value.strip().casefold() in ("yes", "no"):
        return value.strip().casefold()
    return None


def _check_floor(row: MarketRow, facts: JobFacts) -> FieldCheck:
    unverified = FieldCheck(Verification.UNVERIFIED, row.floor_cell)
    if row.floor is None or facts.floor_amount is None or not facts.floor_currency:
        return unverified
    same = (facts.floor_amount == row.floor.amount and facts.floor_currency.strip().upper() == row.floor.currency
            and facts.floor_basis in (None, row.floor.basis))
    return FieldCheck(Verification.VERIFIED, row.floor_cell, row.floor) if same else FieldCheck(Verification.CONFLICT, row.floor_cell)


def _check_need(row: MarketRow, facts: JobFacts) -> FieldCheck:
    agent = _agent_need(facts.needed)
    if row.need is None or agent is None:
        return FieldCheck(Verification.UNVERIFIED, row.sponsorship_cell)
    if agent == row.need:
        return FieldCheck(Verification.VERIFIED, row.sponsorship_cell, row.need)
    return FieldCheck(Verification.CONFLICT, row.sponsorship_cell)


def verify_market(job_facts: JobFacts, markets: Markets) -> MarketCheck:
    """Match `job_facts.market` to a row and verify the agent's floor and need against it.

    No market named: no row and both inputs unverified. A market naming no row (or an ambiguous one): `MarketError`
    (EVL006).
    """
    name = (job_facts.market or "").strip()
    if not name:
        none = FieldCheck(Verification.UNVERIFIED, None)
        return MarketCheck(None, none, none)
    key = norm_label(name)
    if key in markets.ambiguous:
        raise MarketError(f"market {name!r} is ambiguous: the Markets table lists it more than once")
    row = markets.rows.get(key)
    if row is None:
        raise MarketError(f"market {name!r} names no row of the Markets table")
    return MarketCheck(row, _check_floor(row, job_facts), _check_need(row, job_facts))
