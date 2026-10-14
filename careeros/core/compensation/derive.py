"""Pure compensation derivation (spec section 5). No I/O, no clock, no markets parsing; `now` is never an input.

`derive_compensation(record, floor)` annualises each observation (month x12 first), converts it with the single exact
`(observation currency -> floor currency)` conversion, and marks it comparable or gives it one stable reason code. The
first applicable reason is reported, in this order: `FLOOR_UNVERIFIED`, `BASIS_MISMATCH`, `FX_CONVERSION_MISSING`,
`FX_RATE_OUTSIDE_WINDOW`. A not-comparable observation keeps its originals but has no `converted` figure and never
contributes to a range, a conflict or a cap. Date arithmetic is on `datetime.date` differences only.

The effective kind is the highest of `rules.KINDS` present among all observations, comparable or not (no fall-back to
a lower kind). Among that kind's comparable observations the effective range is their union (`None` = open side),
provided they share a common intersection (closed intervals; touching counts); otherwise the status is `conflict`.
Status is `none` for an empty record or when no observation of the effective kind is comparable.

`kind_complete`: every observation of the effective kind is comparable. `kind_confident`: none of them is `low`. Both
are False for an empty record (there is no kind to speak of). Observations are returned sorted by id, and notes in a
fixed order, so the result and `to_basis` do not depend on input order. Exact `Fraction`s throughout; nothing rounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from careeros.core.compensation import rules
from careeros.core.compensation.model import CompRecord, Observation
from careeros.core.evaluation import canon

FX_WINDOW_DAYS = rules.FX_WINDOW_DAYS
PERIOD_FACTOR = {"year": 1, "month": 12}


@dataclass(frozen=True)
class FloorInfo:
    amount: int | None
    currency: str | None
    basis: str | None
    verified: bool


@dataclass(frozen=True)
class Original:
    low: int | None
    high: int | None
    currency: str
    period: str
    source: str


@dataclass(frozen=True)
class Span:
    low: Fraction | None
    high: Fraction | None


@dataclass(frozen=True)
class ObsResult:
    id: str
    kind: str
    original: Original
    annualised: Span
    converted: Span | None          # in the floor's currency; None when not comparable
    comparable: bool
    reason: str | None
    confidence: str


@dataclass(frozen=True)
class Note:
    code: str                       # estimate_only | conflict | incomparable
    ref: str | None                 # observation id for `incomparable`, else None
    detail: str                     # the CMP reason code for `incomparable`, else the kind


@dataclass(frozen=True)
class Derivation:
    observations: tuple[ObsResult, ...]
    status: str                     # effective | none | conflict
    kind: str | None
    low: Fraction | None
    high: Fraction | None
    kind_complete: bool
    kind_confident: bool
    notes: tuple[Note, ...]


def _scale(value: int | None, factor: Fraction | int) -> Fraction | None:
    return None if value is None else Fraction(value) * factor


def _one(o: Observation, floor: FloorInfo, conversions, window: int) -> ObsResult:
    annual = Span(_scale(o.low, PERIOD_FACTOR[o.period]), _scale(o.high, PERIOD_FACTOR[o.period]))
    original = Original(o.low, o.high, o.currency, o.period, o.source)
    reason = None
    rate = None
    if not floor.verified:
        reason = "FLOOR_UNVERIFIED"
    elif o.basis != floor.basis:
        reason = "BASIS_MISMATCH"
    elif o.currency != floor.currency:
        match = [c for c in conversions if c.from_currency == o.currency and c.to_currency == floor.currency]
        if not match:
            reason = "FX_CONVERSION_MISSING"
        elif not 0 <= (o.as_of - match[0].as_of).days <= window:
            reason = "FX_RATE_OUTSIDE_WINDOW"
        else:
            rate = match[0].rate
    converted = None
    if reason is None:
        factor = 1 if rate is None else rate
        converted = Span(_scale(annual.low, factor), _scale(annual.high, factor))
    return ObsResult(o.id, o.kind, original, annual, converted, reason is None, reason, o.confidence)


def _union(spans: list[Span]) -> tuple[bool, Fraction | None, Fraction | None]:
    """(consistent, low, high). Intervals share a point iff the largest low <= the smallest high (None = unbounded)."""
    lows = [s.low for s in spans if s.low is not None]
    highs = [s.high for s in spans if s.high is not None]
    if lows and highs and max(lows) > min(highs):
        return False, None, None
    low = None if any(s.low is None for s in spans) else min(lows)
    high = None if any(s.high is None for s in spans) else max(highs)
    return True, low, high


def derive_compensation(record: CompRecord, floor: FloorInfo, *, fx_window_days: int = FX_WINDOW_DAYS) -> Derivation:
    results = tuple(sorted((_one(o, floor, record.conversions, fx_window_days) for o in record.observations),
                           key=lambda r: r.id))
    notes = [Note("incomparable", r.id, r.reason) for r in results if not r.comparable]
    if not results:
        return Derivation((), "none", None, None, None, False, False, ())
    kind = next(k for k in rules.KINDS if any(r.kind == k for r in results))
    same = [r for r in results if r.kind == kind]
    comparable = [r for r in same if r.comparable]
    complete = len(comparable) == len(same)
    confident = all(r.confidence != "low" for r in same)
    status, low, high = "none", None, None
    if comparable:
        ok, low, high = _union([r.converted for r in comparable])
        if ok:
            status = "effective"
            if kind == "estimate":
                notes.append(Note("estimate_only", None, kind))
        else:
            status = "conflict"
            notes.append(Note("conflict", None, kind))
    notes.sort(key=lambda n: (n.code, n.ref or "", n.detail))
    return Derivation(results, status, kind, low, high, complete, confident, tuple(notes))


def _exact(value: Fraction | None) -> int | str | None:
    return None if value is None else canon.exact(value)


def to_basis(d: Derivation) -> dict:
    """The JSON-safe form stored in `cap_inputs.compensation`: exact strings, no floats, no dates."""
    return {
        "status": d.status,
        "kind": d.kind,
        "low": _exact(d.low),
        "high": _exact(d.high),
        "kind_complete": d.kind_complete,
        "kind_confident": d.kind_confident,
        "observations": [{"id": r.id, "kind": r.kind, "comparable": r.comparable, "reason": r.reason}
                         for r in d.observations],
        "notes": [{"code": n.code, "ref": n.ref, "detail": n.detail} for n in d.notes],
    }
