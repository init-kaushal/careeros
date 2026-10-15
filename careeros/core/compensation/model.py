"""Data shapes of a parsed compensation record. Frozen and hashable; no behaviour, no I/O.

Dates are `datetime.date` (validated at parse; canonical forms render them as ISO strings). Amounts are whole integers
in whole currency units or `None` for an undisclosed side (never zero). A conversion `rate` is an exact `Fraction`.
Findings reuse `careeros.core.models.Issue(severity, code, path, message, fix)`, the class `validation` re-exports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from careeros.core.evaluation.model import MarketBasis


@dataclass(frozen=True)
class Observation:
    id: str
    kind: str                       # one of rules.KINDS
    low: int | None
    high: int | None
    currency: str                   # stripped and upper-cased
    basis: MarketBasis
    period: str                     # one of rules.PERIODS
    source: str
    quote: str | None
    as_of: date
    confidence: str                 # one of rules.CONFIDENCES


@dataclass(frozen=True)
class Conversion:
    from_currency: str
    to_currency: str
    rate: Fraction                  # positive; units of `to_currency` per unit of `from_currency`
    source: str
    as_of: date


@dataclass(frozen=True)
class CompRecord:
    market: str | None              # exact `## Markets` row label; None only when there are no observations
    observations: tuple[Observation, ...]
    conversions: tuple[Conversion, ...]
    notes: str                      # the free body below the frontmatter; ignored by code
