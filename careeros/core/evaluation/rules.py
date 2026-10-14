"""Scoring rules and basis layouts, each versioned independently. Pure; no I/O.

Every constant is a `fractions.Fraction` (class weights and credits included), so thresholds are compared
on exact values and nothing here can differ across Python versions or float repr. The mappings are
read-only and keyed by the plain strings stored in records (`Outcome`, class and judgement values).

Two registries, looked up independently:

* `RULES` maps a rules version to its constants (`Rules`). `RULES_VERSION` is the version a new evaluation
  uses and stamps into `result.stamps.rules`. Released versions are never removed or edited, so a stored
  result can still be recomputed with the constants it was produced under.
* `BASIS_VERSIONS` maps a basis version to its layout (`BasisSchema`): the shape of `result.basis`, the
  exact normalised inputs of the pure scoring function. A new layout does not require new rules, nor the
  reverse.

Unsupported versions: `rules_for` and `basis_schema_for` raise `UnsupportedVersion` for any version this
CareerOS does not know (older-and-dropped or newer, and any non-string value). That means "cannot be
verified", never "tampered": callers must report it as `EVL010` and must not report `EVL007`. The registries are plain dicts that only tests may mutate. A fresh
`evaluate` always uses the current versions and so replaces such a record without calling it tampered.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType

RULES_VERSION = "1"
BASIS_VERSION = "1"


class UnsupportedVersion(LookupError):
    """A rules or basis version this CareerOS does not know. Means 'cannot be verified' (EVL010), not tamper.

    Deliberately not a `ValueError`: callers that catch `ValueError` for malformed input must not swallow it
    as tamper, and should catch `UnsupportedVersion` first.
    """


def _frozen(**items: int | Fraction) -> Mapping[str, Fraction]:
    return MappingProxyType({k: Fraction(v) for k, v in items.items()})


@dataclass(frozen=True)
class Rules:
    """One released set of scoring constants."""

    # Field names map to the plan/spec constants: coverage_share = COVERAGE_SHARE, fit_share = FIT_SHARE,
    # tier_* = tier cut-offs, class_weights/credits/fit_weights/fit_credits = spec 6.1-6.2 tables,
    # claimed_share/unverifiable_share(_severe)/min_requirements = spec 6.5 thresholds.
    version: str
    coverage_share: Fraction
    fit_share: Fraction
    tier_pursue: Fraction           # combined >= this -> pursue
    tier_stretch: Fraction          # combined >= this -> stretch
    tier_weak: Fraction             # combined >= this -> weak, else skip
    class_weights: Mapping[str, Fraction]
    credits: Mapping[str, Fraction]
    fit_weights: Mapping[str, Fraction]
    fit_credits: Mapping[str, Fraction]
    claimed_share: Fraction             # confidence: more than this share of support weight is claimed-only
    unverifiable_share: Fraction        # confidence: more than this share of must/strong weight unverifiable
    unverifiable_share_severe: Fraction  # ... two steps above this share
    min_requirements: Fraction          # confidence: fewer requirements than this is thin extraction


@dataclass(frozen=True)
class RulesV1(Rules):
    """Rules version 1 (spec sections 4 and 6)."""

    version: str = "1"
    coverage_share: Fraction = Fraction(65, 100)
    fit_share: Fraction = Fraction(35, 100)
    tier_pursue: Fraction = Fraction(7, 10)
    tier_stretch: Fraction = Fraction(1, 2)
    tier_weak: Fraction = Fraction(3, 10)
    class_weights: Mapping[str, Fraction] = field(default_factory=lambda: _frozen(must=3, strong=2, nice=1))
    credits: Mapping[str, Fraction] = field(default_factory=lambda: _frozen(covered=1, adjacent=Fraction(1, 2), gap=0))
    fit_weights: Mapping[str, Fraction] = field(default_factory=lambda: _frozen(high=3, medium=2, low=1))
    fit_credits: Mapping[str, Fraction] = field(
        default_factory=lambda: _frozen(yes=1, partial=Fraction(1, 2), no=0))
    claimed_share: Fraction = Fraction(1, 2)
    unverifiable_share: Fraction = Fraction(1, 4)
    unverifiable_share_severe: Fraction = Fraction(1, 2)
    min_requirements: Fraction = Fraction(3)


@dataclass(frozen=True)
class BasisSchema:
    """The layout of `result.basis` for one basis version: its top-level keys, in canonical documentation order.

    v1 `cap_inputs`: `{market, need: {value, verification, source_cell, row_state}, listing, quote, range: {low, high,
    currency, basis} | null, floor: {amount, currency, basis, verification, source_cell, row_state}, confidence,
    as_of}`; `row_state` is `parseable | unreadable | no_market` (whether the Markets row gave the parser a value,
    recorded at build time so scoring never re-parses a cell).
    """

    version: str
    keys: tuple[str, ...]


RULES: dict[str, Rules] = {"1": RulesV1()}

BASIS_VERSIONS: dict[str, BasisSchema] = {
    "1": BasisSchema(
        version="1",
        keys=("basis_version", "rules_version", "requirement_count", "rubric_state", "requirements", "fit", "cap_inputs"),
    ),
}


def rules_for(version: str) -> Rules:
    """The constants of a rules version; `UnsupportedVersion` when this CareerOS does not know it."""
    try:
        return RULES[version]
    except (KeyError, TypeError):
        raise UnsupportedVersion(version) from None


def basis_schema_for(version: str) -> BasisSchema:
    """The layout of a basis version; `UnsupportedVersion` when this CareerOS does not know it."""
    try:
        return BASIS_VERSIONS[version]
    except (KeyError, TypeError):
        raise UnsupportedVersion(version) from None
