"""Data shapes shared by every evaluation module. Frozen, pure, no I/O.

Enum values are the exact strings stored in `evaluation.md` and the ledger; tests pin them so a typo cannot
silently change a stored value. Exact quantities are never floats here: weights are integers and any
non-integer quantity is a `Fraction` in memory and a string fraction (`"1/2"`) when hashed (`canon.exact`).

`Cap.code` values are pinned by tests. YAML 1.1 turns unquoted `yes`/`no` into booleans, so the record parser
converts them to the strings before building `Judgement` or the sponsorship `needed` value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    COVERED = "covered"
    ADJACENT = "adjacent"
    GAP = "gap"
    UNVERIFIABLE = "unverifiable"


class Tier(StrEnum):
    PURSUE = "pursue"
    STRETCH = "stretch"
    WEAK = "weak"
    SKIP = "skip"
    UNRATED = "unrated"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReqClass(StrEnum):
    MUST = "must"
    STRONG = "strong"
    NICE = "nice"


class Kind(StrEnum):
    TECHNOLOGY = "technology"
    TITLE = "title"
    CERTIFICATION = "certification"
    EDUCATION = "education"
    YEARS = "years"
    DOMAIN = "domain"
    OTHER = "other"


class Strength(StrEnum):
    DIRECT = "direct"
    ADJACENT = "adjacent"


class Judgement(StrEnum):
    YES = "yes"
    PARTIAL = "partial"
    NO = "no"


class Listing(StrEnum):
    OFFERED = "offered"
    NOT_OFFERED = "not_offered"
    UNKNOWN = "unknown"


class Verification(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICT = "conflict"


class MarketBasis(StrEnum):
    BASE = "base"
    TOTAL = "total"


class SourceConfidence(StrEnum):
    """Confidence in a compensation figure's source (not the result confidence)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Link:
    """An agent-supplied pointer from a requirement to a fact; an input, never a conclusion."""

    fact: str
    strength: Strength
    why: str


@dataclass(frozen=True)
class Requirement:
    """One requirement the agent extracted from the posting. `cls` is the YAML key `class`."""

    id: str
    cls: ReqClass
    kind: Kind
    text: str
    quote: str
    term: str | None = None
    years: int | None = None
    links: tuple[Link, ...] = ()


@dataclass(frozen=True)
class FitJudgement:
    """The agent's judgement of one fit row; `row` is the exact rubric row text."""

    row: str
    judgement: Judgement
    quote: str | None


@dataclass(frozen=True)
class JobFacts:
    """What the agent read from the posting about sponsorship and pay (spec 4.2); all optional."""

    market: str | None = None
    needed: bool | None = None
    listing: Listing = Listing.UNKNOWN
    sponsorship_quote: str | None = None
    range_low: int | None = None
    range_high: int | None = None
    range_currency: str | None = None
    range_basis: MarketBasis | None = None
    floor_amount: int | None = None
    floor_currency: str | None = None
    floor_basis: MarketBasis | None = None
    source: str | None = None
    comp_confidence: SourceConfidence | None = None
    as_of: str | None = None


@dataclass(frozen=True)
class Cap:
    """An applied cap: the best tier the posting may reach, and why."""

    code: str
    ceiling: Tier
    reason: str


@dataclass(frozen=True)
class ActionItem:
    """What would change the result; `ref` names the requirement or fact it concerns."""

    ref: str
    message: str


@dataclass(frozen=True)
class Stamps:
    """Freshness stamps: hashes (rules: the version) of the inputs the result was computed from."""

    jd: str
    memory: str
    rubric: str
    rules: str
    inputs: str
