"""Requirement outcomes derived from Career Memory (spec 5). Pure: reads a loaded `Career`, writes nothing.

`covered` is reserved for a deterministic match in the one field relevant to the requirement's kind among the
active facts (`claimed`, `confirmed`, `verified`). An agent link is an input, never a conclusion: a valid link (it
names an existing active fact) only upgrades `gap` to `adjacent`, whatever its strength. A link that names a missing,
`disputed` or `retired` fact raises `DeriveError` (EVL005) for every kind of requirement, before anything is derived.

Reuse: whole years come from `check.Memory.months` (the union of active dated experience intervals) and title
variants from `Memory` records; technologies and certifications use the same canonical forms the evidence check does.

`status_floor` is the weakest status among the (at most five, sorted by id) facts listed for a requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from careeros.core.evaluation.model import Kind, Outcome, Requirement
from careeros.core.evaluation.rules import BASIS_VERSION, RULES_VERSION
from careeros.core.memory import store
from careeros.core.memory.check import Memory
from careeros.core.memory.lexicon import Lexicon
from careeros.core.memory.normalize import norm_text
from careeros.core.memory.store import Career
from careeros.core.workspace import WorkspaceError

MAX_FACTS = 5
_STATUS_ORDER = {"claimed": 0, "confirmed": 1, "verified": 2}
_TECH_KINDS = ("skill", "experience", "project", "achievement")


class DeriveError(WorkspaceError):
    """An agent link names a fact that is missing or not active (EVL005)."""

    def __init__(self, requirement_id: str, message: str, code: str = "EVL005") -> None:
        super().__init__(message)
        self.code = code
        self.requirement_id = requirement_id


@dataclass(frozen=True)
class Derivation:
    id: str
    outcome: Outcome
    facts: tuple[str, ...] = ()
    status_floor: str | None = None
    reason: str | None = None            # set for `unverifiable`
    link_strength: str | None = None     # recorded for reviewers; never changes the outcome


@dataclass(frozen=True)
class Basis:
    """The memory-derived part of `result.basis`: requirement entries in id order. Fit and cap inputs are added later."""

    requirements: tuple[dict, ...]
    requirement_count: int
    basis_version: str = BASIS_VERSION
    rules_version: str = RULES_VERSION

    def as_dict(self) -> dict:
        return {
            "basis_version": self.basis_version,
            "rules_version": self.rules_version,
            "requirement_count": self.requirement_count,
            "requirements": [dict(r, facts=list(r["facts"])) for r in self.requirements],
        }


@dataclass(frozen=True)
class Derived:
    items: tuple[Derivation, ...]         # in the order the requirements were given
    basis: Basis = field(compare=False)


def _listed(ids: set[str], by_id: dict) -> tuple[tuple[str, ...], str | None]:
    facts = tuple(sorted(ids))[:MAX_FACTS]
    floor = min((by_id[i].status for i in facts), key=_STATUS_ORDER.__getitem__, default=None)
    return facts, floor


def _norm_cert(lexicon: Lexicon, name: str) -> str:
    return (lexicon.certs.get(name.lower()) or name).lower()


def _match(req: Requirement, career: Career, lexicon: Lexicon, memory: Memory) -> set[str]:
    """Ids of the active facts that establish `req` deterministically (empty: no match)."""
    term = (req.term or "").strip()
    if req.kind == Kind.TECHNOLOGY and term:
        want = norm_text(lexicon.canonical_tech(term) or term)
        return {r.id for r in memory.records if r.kind in _TECH_KINDS and any(norm_text(str(t)) == want for t in r.techs)}
    if req.kind == Kind.TITLE and term:
        want = norm_text(term)
        return {r.id for r in memory.records if r.kind in ("experience", "identity") and want in r.titles}
    if req.kind == Kind.CERTIFICATION and term:
        want = _norm_cert(lexicon, term)
        return {r.id for r in memory.records if r.kind == "certification" and want in r.certs}
    if req.kind == Kind.EDUCATION and term:
        words = set(norm_text(term).split())
        if not words:
            return set()
        return {f.id for f in career.active() if f.kind == "education"
                and words <= set(norm_text(f"{f.get('degree') or ''} {f.get('field') or ''}").split())}
    return set()


def _unverifiable(req: Requirement) -> str | None:
    if req.kind != Kind.YEARS:
        return None
    if req.term and req.term.strip():
        return f"years of {req.term.strip()} cannot be derived from dated experience"
    if req.years is None or isinstance(req.years, bool) or req.years <= 0:
        return "the number of years is missing or not positive"
    return None


def derive(requirements: tuple[Requirement, ...], career: Career, lexicon: Lexicon, now: str) -> Derived:
    by_id = career.by_id()
    active = {f.id for f in career.active()}
    for req in requirements:
        for link in req.links:
            if link.fact not in active:
                state = by_id[link.fact].status if link.fact in by_id else "missing"
                raise DeriveError(req.id, f"{req.id}: link to {link.fact!r} is not an active fact ({state})")
    memory = Memory(career, lexicon, now)
    experience_ids = {f.id for f in career.active() if f.kind == "experience"}
    items: list[Derivation] = []
    for req in requirements:
        strength = ("direct" if any(str(l.strength) == "direct" for l in req.links) else "adjacent") if req.links else None
        reason = _unverifiable(req)
        matched = set() if reason else _match(req, career, lexicon, memory)
        if req.kind == Kind.YEARS and not reason:
            matched = experience_ids if req.years <= memory.months // 12 else set()
        if reason:
            items.append(Derivation(req.id, Outcome.UNVERIFIABLE, reason=reason, link_strength=strength))
        elif matched:
            facts, floor = _listed(matched, by_id)
            items.append(Derivation(req.id, Outcome.COVERED, facts, floor, link_strength=strength))
        elif req.links:
            facts, floor = _listed({l.fact for l in req.links}, by_id)
            items.append(Derivation(req.id, Outcome.ADJACENT, facts, floor, link_strength=strength))
        else:
            items.append(Derivation(req.id, Outcome.GAP))
    classes = {r.id: str(r.cls) for r in requirements}
    entries = tuple(
        {"id": d.id, "class": classes[d.id], "outcome": str(d.outcome), "facts": d.facts, "status_floor": d.status_floor}
        for d in sorted(items, key=lambda d: d.id))
    return Derived(tuple(items), Basis(entries, len(requirements)))
