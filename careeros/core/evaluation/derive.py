"""Requirement outcomes derived from Career Memory (spec 5). Pure: reads a loaded `Career`, writes nothing.

`covered` is reserved for a deterministic match in the one field relevant to the requirement's kind among the
active facts (`claimed`, `confirmed`, `verified`). An agent link is an input, never a conclusion: a valid link (it
names an existing active fact) only upgrades `gap` to `adjacent`, whatever its strength. A link that names a missing,
`disputed` or `retired` fact raises `DeriveError` (EVL005) for every kind of requirement, before anything is derived.

Reuse: whole years come from `check.Memory.months` (the union of active dated experience intervals) and title
variants from `Memory` records; technologies and certifications use the same canonical forms the evidence check does.

An achievement whose parent experience or project is not active (retired, disputed, or missing) is inactive too: it
supports nothing and is an invalid link target (EVL005). Technologies compare as exact casefolded canonical strings
(`C++`, `C` and `C#` are different; `.NET` is not `NET`); no punctuation is dropped.

Known limits: education matching is a word-subset test on `degree` + `field`, so generic words (`in`, `Science`) can
match; a market label elsewhere must match its table row exactly.

`status_floor` is the weakest status among the (at most five, sorted by id) facts listed for a requirement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from careeros.core.evaluation.markets import MarketCheck, _agent_need
from careeros.core.evaluation.model import FitJudgement, JobFacts, Kind, Outcome, Requirement, Verification
from careeros.core.evaluation.rubric import Rubric, norm_label
from careeros.core.evaluation.rules import BASIS_VERSION, RULES_VERSION
from careeros.core.memory import store
from careeros.core.memory.check import Memory, _year_month
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

    def complete(self, fit: tuple[FitJudgement, ...], facts: JobFacts, check: MarketCheck, rubric: Rubric) -> dict:
        """The full BasisSchema-v1 basis: `as_dict()` plus `rubric_state`, `fit` and `cap_inputs`.

        Fit rows are the usable rubric's rows that have a judgement, sorted by normalised label (a judgement naming no
        rubric row is ignored here; the record validator rejects it). An unusable rubric gives `fit: []` and state
        `missing` or `invalid`. Cap inputs hold the agent's values (display only unless `verified`) with the
        verification states and exact source cells from `check`; a range needs an integer `high`.
        """
        by_row = {norm_label(j.row): j for j in fit}
        rows = sorted(({"row": r.key, "weight": r.weight, "judgement": str(by_row[r.key].judgement)}
                       for r in rubric.rows if r.key in by_row), key=lambda r: r["row"]) if rubric.usable else []
        return {**self.as_dict(), "rubric_state": rubric.state if rubric.usable or rubric.state != "ok" else "missing",
                "fit": rows, "cap_inputs": _cap_inputs(facts, check)}


def _currency(value: str | None) -> str | None:
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def _cap_inputs(facts: JobFacts, check: MarketCheck) -> dict:
    """Need and floor are always stored (even when the agent gave nothing), with the exact Markets source cell."""
    rng = None
    if type(facts.range_high) is int:
        rng = {"low": facts.range_low if type(facts.range_low) is int else None, "high": facts.range_high,
               "currency": _currency(facts.range_currency),
               "basis": str(facts.range_basis) if facts.range_basis else None}
    given = type(facts.floor_amount) is int
    verified = check.floor.state == Verification.VERIFIED
    basis = check.floor.value.basis if verified else facts.floor_basis
    return {
        "market": facts.market.strip() if facts.market and facts.market.strip() else None,
        "need": {"value": _agent_need(facts.needed), "verification": str(check.need.state),
                 "source_cell": check.need.source},
        "listing": str(facts.listing),
        "quote": bool(facts.sponsorship_quote and facts.sponsorship_quote.strip()),
        "range": rng,
        "floor": {"amount": facts.floor_amount if given else None,
                  "currency": _currency(facts.floor_currency) if given else None,
                  "basis": str(basis) if given and basis else None,
                  "verification": str(check.floor.state), "source_cell": check.floor.source},
        "confidence": str(facts.comp_confidence) if facts.comp_confidence else None,
        "as_of": facts.as_of,
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


def _usable(career: Career) -> set[str]:
    """Ids of active facts that may support anything: an achievement also needs an active parent (if it has one)."""
    by_id = career.by_id()
    usable: set[str] = set()
    for fact in career.active():
        parent = fact.get("parent") if fact.kind == "achievement" else None
        if parent is not None and (parent not in by_id or by_id[parent].status not in store.ACTIVE):
            continue
        usable.add(fact.id)
    return usable


def _contributing(career: Career, memory: Memory) -> set[str]:
    """Active experiences whose dates add months to `Memory.months`."""
    out: set[str] = set()
    for f in career.active():
        if f.kind == "experience":
            begin = _year_month(str(f.get("start")), memory.now_year, memory.now_month)
            finish = _year_month(str(f.get("end")), memory.now_year, memory.now_month)
            if begin is not None and finish is not None and finish > begin:
                out.add(f.id)
    return out


def _match(req: Requirement, career: Career, lexicon: Lexicon, memory: Memory, usable: set[str]) -> set[str]:
    """Ids of the active facts that establish `req` deterministically (empty: no match)."""
    term = (req.term or "").strip()
    if req.kind == Kind.TECHNOLOGY and term:
        want = (lexicon.canonical_tech(term) or term).strip().casefold()
        return {r.id for r in memory.records if r.id in usable and r.kind in _TECH_KINDS
                and any(str(t).strip().casefold() == want for t in r.techs)}
    if req.kind == Kind.TITLE and term:
        want = norm_text(term)
        return {r.id for r in memory.records if r.id in usable and r.kind in ("experience", "identity") and want in r.titles}
    if req.kind == Kind.CERTIFICATION and term:
        want = _norm_cert(lexicon, term)
        return {r.id for r in memory.records if r.id in usable and r.kind == "certification" and want in r.certs}
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
    active = _usable(career)
    for req in requirements:
        for link in req.links:
            if link.fact not in active:
                state = ("missing" if link.fact not in by_id else by_id[link.fact].status
                         if by_id[link.fact].status not in store.ACTIVE else "its parent is not active")
                raise DeriveError(req.id, f"{req.id}: link to {link.fact!r} is not an active fact ({state})")
    memory = Memory(career, lexicon, now)
    experience_ids = _contributing(career, memory)
    items: list[Derivation] = []
    for req in requirements:
        strength = ("direct" if any(str(l.strength) == "direct" for l in req.links) else "adjacent") if req.links else None
        reason = _unverifiable(req)
        matched = set() if reason else _match(req, career, lexicon, memory, active)
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
