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

from careeros.core.compensation.derive import FloorInfo
from careeros.core.evaluation.markets import MarketCheck, _agent_need, norm_basis, norm_currency
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

    def complete(self, fit: tuple[FitJudgement, ...], facts: JobFacts, check: MarketCheck, rubric: Rubric,
                 compensation: dict | None = None, floor: FloorInfo | None = None) -> dict:
        """The full basis: `as_dict()` plus `rubric_state`, `fit` and `cap_inputs`.

        Fit rows are the usable rubric's rows that have a judgement, sorted by normalised label (a judgement naming no
        rubric row is ignored here; the record validator rejects it). An unusable rubric gives `fit: []` and state
        `missing` or `invalid`.

        With `compensation` (the `compensation.derive.to_basis` object) and `floor` (the `FloorInfo` of the Markets row
        named by `job_facts.market`): the BasisSchema-v2 layout under this basis' versions; the floor is the row's own,
        `verified` or `unverified` (`_cap_inputs_v2`). Without them: the BasisSchema-v1 layout, stamped basis/rules
        version "1" whatever this basis says (the layout decides the version; kept for the v1 layout tests and the
        scorer's v1 path): the agent's values (display only unless `verified`) with the verification states and exact
        source cells from `check`; a range needs an integer `high`.
        """
        by_row = {norm_label(j.row): j for j in fit}
        rows = sorted(({"row": r.key, "weight": r.weight, "judgement": str(by_row[r.key].judgement)}
                       for r in rubric.rows if r.key in by_row), key=lambda r: r["row"]) if rubric.usable else []
        head = self.as_dict()
        if compensation is None:
            head.update(basis_version="1", rules_version="1")
            cap_inputs = _cap_inputs(facts, check)
        else:
            cap_inputs = _cap_inputs_v2(facts, check, floor or FloorInfo(None, None, None, False), compensation)
        return {**head, "rubric_state": rubric.state if rubric.usable or rubric.state != "ok" else "missing",
                "fit": rows, "cap_inputs": cap_inputs}


_CONFIDENCES = ("high", "medium", "low", "unknown")


def _row_state(check: MarketCheck, parsed) -> str:
    """Whether the Markets row gave the parser something to read, recorded so the scorer never re-parses a cell."""
    if check.row is None:
        return "no_market"
    if check.unusable:
        return "unreadable"
    return "parseable" if parsed is not None else "unreadable"


def _cap_inputs(facts: JobFacts, check: MarketCheck) -> dict:
    """Need and floor are always stored (even when the agent gave nothing), with the exact Markets source cell."""
    rng = None
    if type(facts.range_high) is int:
        rng = {"low": facts.range_low if type(facts.range_low) is int else None, "high": facts.range_high,
               "currency": norm_currency(facts.range_currency), "basis": norm_basis(facts.range_basis)}
    amount = facts.floor_amount if type(facts.floor_amount) is int else None
    currency = norm_currency(facts.floor_currency)
    verified = check.floor.state == Verification.VERIFIED
    basis = str(check.floor.value.basis) if verified else norm_basis(facts.floor_basis)
    return {
        "market": facts.market.strip() if facts.market and facts.market.strip() else None,
        "need": {"value": _agent_need(facts.needed), "verification": str(check.need.state),
                 "source_cell": check.need.source,
                 "row_state": _row_state(check, check.row.need if check.row else None)},
        "listing": str(facts.listing) if str(facts.listing) in ("offered", "not_offered", "unknown") else "unknown",
        "quote": bool(facts.sponsorship_quote and facts.sponsorship_quote.strip()),
        "range": rng,
        "floor": {"amount": amount, "currency": currency,
                  "basis": basis if amount is not None or currency else None,
                  "verification": str(check.floor.state), "source_cell": check.floor.source,
                  "row_state": _row_state(check, check.row.floor if check.row else None)},
        "confidence": str(facts.comp_confidence) if str(facts.comp_confidence) in _CONFIDENCES else None,
        "as_of": facts.as_of if isinstance(facts.as_of, str) else None,
    }


def _cap_inputs_v2(facts: JobFacts, check: MarketCheck, floor: FloorInfo, compensation: dict) -> dict:
    """BasisSchema-v2 cap inputs: need, listing and quote as in v1; the floor taken from the Markets row by code (no
    agent copy, so `verified` or `unverified`, never `conflict`; an unverified floor stores no amount, currency or
    basis); `compensation` is stored as given (`compensation.derive.to_basis`)."""
    v1 = _cap_inputs(facts, check)
    row = check.row
    verified = floor.verified and row is not None
    return {
        "market": v1["market"], "need": v1["need"], "listing": v1["listing"], "quote": v1["quote"],
        "floor": {"amount": floor.amount if verified else None, "currency": floor.currency if verified else None,
                  "basis": floor.basis if verified else None,
                  "verification": str(Verification.VERIFIED if verified else Verification.UNVERIFIED),
                  "source_cell": row.floor_cell if row is not None else None,
                  "row_state": "no_market" if row is None else "parseable" if verified else "unreadable"},
        "compensation": compensation,
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
