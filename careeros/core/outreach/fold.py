"""Person flags, target decisions and displayed target status folded from ledger events (spec sections 5 and 6). Pure.

Nothing here reads the clock or the filesystem: every function that needs the time takes `now` (a UTC timestamp string
such as `2026-10-19T09:00:00Z`). Inputs are plain data: events (dicts), records parsed by `person.parse` and
`target.parse`, and `JobInfo` values built by the caller.

* Events are applied in `seq` order. `outreach.rejected` is audit-only and ignored wherever it appears. An unknown
  `outreach.*` type, an event with no usable entity or timestamp, and a sequence the transition tables refuse are not
  applied and are returned in `errors` (the source of `OUT005`); they are never dropped silently. The same holds for an
  outcome event (`accepted`, `replied`, `closed`) whose `source` is unsupported or whose closed `reason` is invalid: it
  is reported and changes no derived state (no flag, no `last_activity`), and it does not use up the person's single
  outcome of that kind, so a later valid event still applies.
* Person flags come from the ledger only: `invited_at` is the earliest consumed `linkedin_invite` (a second consumed
  invite is reported), `messaged_at` the latest consumed `linkedin_message`, and `accepted_at`, `replied_at` and
  `closed_at` from the single outcome event of each kind. The approval events are matched to the person through the
  ledger-recorded person id of the item (`approvals.fold`), and a `consumed` event counts only when it was a legal
  transition at its place in the sequence. Outcome legality is checked at the event's place in `seq` order.
* A target's decision comes from its events (`target_added` carries the slug in `action`, see `target_added_fields`).
  Its displayed status also reads validated record fields (the person records' `found_in`, the saved jobs' company), as
  the spec's derivation table says; a hand edit of those can change the status but never identity, sends or closed state.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import timedelta

from careeros.core import ids
from careeros.core.approvals import fold as afold
from careeros.core.approvals import people as apeople
from careeros.core.approvals import rules as arules
from careeros.core.approvals.model import SequenceError, State
from careeros.core.outreach import rules
from careeros.core.outreach.model import JobInfo, Person, PersonFlags, Target, TargetView
from careeros.core.outreach.slug import slugify

_FMT = "%Y-%m-%dT%H:%M:%SZ"
_SHA = re.compile(r"sha256:[0-9a-f]{64}")
_TARGET_ACTION = re.compile(r"add target (\S+)")
_PERSON_KINDS = (arules.LINKEDIN_INVITE, arules.LINKEDIN_MESSAGE)


@dataclass(frozen=True)
class Refusal:
    """A transition the table refuses: a stable code from `rules.REFUSAL_CODES` and the label or status it was refused in."""
    code: str
    state: str


@dataclass(frozen=True)
class PeopleFold:
    flags: dict[str, PersonFlags]
    errors: tuple[SequenceError, ...]


@dataclass(frozen=True)
class TargetsFold:
    targets: dict[str, TargetView]      # by slug, in order of first appearance
    errors: tuple[SequenceError, ...]


# --- transitions -------------------------------------------------------------------------------------------------------

def apply_outcome(flags: PersonFlags, outcome: str, ts: str, reason: str | None = None) -> PersonFlags | Refusal:
    """`flags` with the outcome applied at `ts`, or a `wrong_state` `Refusal` (`state` is the current label). Raises
    ValueError for an unknown outcome. `reason` is kept only for `closed`."""
    if outcome not in rules.OUTCOMES:
        raise ValueError(f"unknown outreach outcome {outcome!r}")
    needs, blocked = rules.OUTCOME_NEEDS_ANY[outcome], rules.OUTCOME_BLOCKED_BY[outcome]
    if (needs and all(getattr(flags, f) is None for f in needs)) or any(getattr(flags, f) is not None for f in blocked):
        return Refusal(rules.WRONG_STATE, flags.label)
    changes: dict = {f"{outcome}_at": ts}
    if outcome == rules.CLOSED:
        changes["closed_reason"] = reason
    else:
        changes["last_activity"] = _later(flags.last_activity, ts)
    return replace(flags, **changes)


def apply_decision(status: str, decision: str) -> str | Refusal:
    """The decision state `decision` (`approve` or `drop`) leads to from `status`, or a `wrong_state` `Refusal`. Raises
    ValueError for an unknown status or decision."""
    if decision not in rules.DECISION_OPERATIONS:
        raise ValueError(f"unknown target decision {decision!r}")
    if status not in rules.DECISIONS:
        raise ValueError(f"unknown target decision state {status!r}")
    new = rules.DECISION_TRANSITIONS.get((status, decision))
    return new if new is not None else Refusal(rules.WRONG_STATE, status)


def target_added_fields(rel: str, sha: str, slug: str) -> dict:
    """The `action` and `artifacts` an `outreach.target_added` event carries (the slug is read back from `action`)."""
    if not isinstance(rel, str) or not rel:
        raise ValueError("the record path must be a non-empty string")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("sha must be 64 lower-case hex characters")
    if not isinstance(slug, str) or not slug or slugify(slug) != slug:
        raise ValueError("slug must be in slugify form")
    return {"action": f"add target {slug}", "artifacts": [rel, f"sha256:{sha}"]}


# --- helpers -------------------------------------------------------------------------------------------------------------

def _later(a: str | None, b: str | None) -> str | None:
    """The later of two timestamps in the one fixed-width format (text order is time order)."""
    return b if a is None else a if b is None else max(a, b)


def _order(indexed: tuple[int, object]) -> tuple:
    index, event = indexed
    seq = event.get("seq")
    return (0, seq, index) if isinstance(seq, int) and not isinstance(seq, bool) else (1, 0, index)


def _ordered(events: Iterable[object]) -> tuple[list[dict], list[SequenceError]]:
    kept, errors = [], []
    for index, event in enumerate(events):
        if isinstance(event, dict):
            kept.append((index, event))
        else:
            errors.append(SequenceError(None, None, f"event {index} is not an object ({type(event).__name__})"))
    kept.sort(key=_order)
    return [e for _, e in kept], errors


def _entity(event: dict) -> str | None:
    return event.get("entity") if isinstance(event.get("entity"), str) else None


def _event_ts(event: dict, errors: list[SequenceError]) -> str | None:
    if afold.parse_ts(event.get("ts")) is None:
        errors.append(SequenceError(event.get("seq"), _entity(event), f"{event.get('type')} has an unreadable ts"))
        return None
    return event["ts"]


# --- people ----------------------------------------------------------------------------------------------------------------

def _outcome_shape_problems(event: dict, outcome: str) -> list[str]:
    problems = []
    if event.get("source") not in rules.OUTCOME_SOURCES:
        problems.append(f"source must be one of {', '.join(rules.OUTCOME_SOURCES)}")
    if outcome == rules.CLOSED and event.get("reason") not in rules.CLOSED_REASONS:
        problems.append(f"a closed event needs a reason from {', '.join(rules.CLOSED_REASONS)}")
    return problems


def fold_people(events: Iterable[object], now: str) -> PeopleFold:
    """Flags for every person that has a `person_added`, an outcome, or an invite or message item in the ledger, plus
    every problem found (the source of `OUT005`)."""
    ordered, errors = _ordered(events)
    items = afold.fold_all(ordered, now)
    flags: dict[str, PersonFlags] = {}
    for st in items.values():
        if st.kind in _PERSON_KINDS and ids.is_valid_id("person", st.target_id):
            current = flags.setdefault(st.target_id, PersonFlags())
            if st.kind == arules.LINKEDIN_INVITE and st.state is State.IN_FLIGHT:
                flags[st.target_id] = replace(current, invite_in_flight=True)
    raw: dict[str, str] = {}      # per item: the raw approvals state, to tell the legal `consumed` event from a stray one
    for event in ordered:
        etype, entity = event.get("type"), _entity(event)
        if not isinstance(etype, str):
            continue
        if etype in arules.STATE_EVENTS and entity is not None:
            if etype == arules.EV_PROPOSED:
                raw.setdefault(entity, arules.PENDING)
                continue
            new = arules.TRANSITIONS.get((raw.get(entity), arules.OPERATION_FOR_EVENT[etype]))
            if new is None:
                continue
            raw[entity] = new
            if etype == arules.EV_CONSUMED:
                _consume(event, items.get(entity), flags, errors)
        elif etype.startswith(rules.EVENT_PREFIX):
            _person_event(event, etype, entity, flags, errors)
    return PeopleFold(flags, tuple(errors))


def _consume(event: dict, st, flags: dict[str, PersonFlags], errors: list[SequenceError]) -> None:
    if st is None or st.kind not in _PERSON_KINDS or not ids.is_valid_id("person", st.target_id):
        return
    per, entity = st.target_id, _entity(event)
    at = _event_ts(event, errors)
    if at is None:
        return
    current = flags.setdefault(per, PersonFlags())
    if st.kind == arules.LINKEDIN_INVITE:
        if current.invited_at is not None:
            errors.append(SequenceError(event.get("seq"), per, f"a second consumed linkedin_invite ({entity}) for {per}"))
        current = replace(current, invited_at=at if current.invited_at is None else min(current.invited_at, at))
    else:
        current = replace(current, messaged_at=_later(current.messaged_at, at))
    flags[per] = replace(current, last_activity=_later(current.last_activity, at))


def _person_event(event: dict, etype: str, entity: str | None, flags: dict[str, PersonFlags], errors: list[SequenceError]) -> None:
    seq = event.get("seq")
    if etype in rules.AUDIT_ONLY_TYPES or etype in (rules.EV_TARGET_ADDED, rules.EV_TARGET_APPROVED, rules.EV_TARGET_DROPPED):
        return
    if etype not in rules.STATE_EVENTS:
        errors.append(SequenceError(seq, entity, f"unknown outreach event type {etype!r}"))
        return
    if not ids.is_valid_id("person", entity):
        errors.append(SequenceError(seq, entity, f"{etype} does not name a person id (entity {event.get('entity')!r})"))
        return
    at = _event_ts(event, errors)
    if at is None:
        return
    if etype == rules.EV_PERSON_ADDED:
        current = flags.setdefault(entity, PersonFlags())
        _, _, problem = apeople.parse_person_added(event)
        if problem:
            errors.append(SequenceError(seq, entity, problem))
        flags[entity] = replace(current, added_at=at if current.added_at is None else min(current.added_at, at),
                                last_activity=_later(current.last_activity, at))
        return
    outcome = rules.OUTCOME_FOR_EVENT[etype]
    problems = _outcome_shape_problems(event, outcome)
    if problems:                        # reported, never applied: a malformed outcome changes no derived state
        errors.extend(SequenceError(seq, entity, f"{etype}: {problem}") for problem in problems)
        return
    current = flags.setdefault(entity, PersonFlags())
    result = apply_outcome(current, outcome, at, event.get("reason") if outcome == rules.CLOSED else None)
    if isinstance(result, Refusal):
        errors.append(SequenceError(seq, entity, f"{etype} is not legal for a {result.state} person"))
    else:
        flags[entity] = result


# --- targets ---------------------------------------------------------------------------------------------------------------

@dataclass
class _Target:
    id: str
    slug: str
    decision: str
    activity: list[str]


def _target_events(ordered: list[dict], errors: list[SequenceError]) -> dict[str, _Target]:
    by_entity: dict[str, _Target] = {}
    slugs: dict[str, str] = {}
    for event in ordered:
        etype, entity, seq = event.get("type"), _entity(event), event.get("seq")
        if etype not in (rules.EV_TARGET_ADDED, rules.EV_TARGET_APPROVED, rules.EV_TARGET_DROPPED):
            continue
        if not ids.is_valid_id("target", entity):
            errors.append(SequenceError(seq, entity, f"{etype} does not name a target id (entity {event.get('entity')!r})"))
            continue
        at = _event_ts(event, errors)
        if at is None:
            continue
        if etype == rules.EV_TARGET_ADDED:
            found = _TARGET_ACTION.fullmatch(event.get("action") if isinstance(event.get("action"), str) else "")
            slug = found[1] if found and slugify(found[1]) == found[1] else None
            arts = event.get("artifacts")
            if slug is None:
                errors.append(SequenceError(seq, entity, f"{etype} action must be `add target <slug>`"))
                continue
            if not (isinstance(arts, list) and len(arts) == 2 and all(isinstance(a, str) for a in arts) and _SHA.fullmatch(arts[1])):
                errors.append(SequenceError(seq, entity, f"{etype} artifacts must be [rel, sha256]"))
            if entity in by_entity:
                errors.append(SequenceError(seq, entity, f"a second {etype} for the same target"))
            elif slug in slugs:
                errors.append(SequenceError(seq, entity, f"{etype} reuses the slug {slug!r} of {slugs[slug]}"))
            else:
                by_entity[entity] = _Target(entity, slug, rules.CANDIDATE, [at])
                slugs[slug] = entity
            continue
        state = by_entity.get(entity)
        if state is None:
            errors.append(SequenceError(seq, entity, f"{etype} before any target_added"))
            continue
        result = apply_decision(state.decision, rules.OPERATION_FOR_EVENT[etype])
        if isinstance(result, Refusal):
            errors.append(SequenceError(seq, entity, f"{etype} is not legal for a {result.state} target"))
            continue
        if etype == rules.EV_TARGET_DROPPED and not str(event.get("reason") or "").strip():
            errors.append(SequenceError(seq, entity, f"{etype} needs a reason"))
        state.decision = result
        if etype == rules.EV_TARGET_APPROVED:
            state.activity.append(at)
    return by_entity


def target_added_ids(events: Iterable[object]) -> dict[str, str]:
    """slug -> target id for every valid `outreach.target_added` event (the id a record file must carry)."""
    ordered, errors = _ordered(events)
    return {t.slug: t.id for t in _target_events(ordered, errors).values()}


def fold_targets(events: Iterable[object], targets: Mapping[str, Target], people: Mapping[str, Person],
                 jobs: Iterable[JobInfo], now: str) -> TargetsFold:
    """A `TargetView` for every target that has a `target_added` event, keyed by slug. `targets` maps slug to the parsed
    record (only its evidence dates are read), `people` maps person id to the parsed record (only `found_in` is read) and
    `jobs` are the saved jobs. Raises ValueError when `now` is unreadable. Person-sequence problems (OUT005) are not
    repeated here: callers call `fold_people` for them."""
    current = afold.parse_ts(now)
    if current is None:
        raise ValueError(f"unreadable now {now!r}")
    ordered, errors = _ordered(events)
    flags = fold_people(ordered, now).flags
    found = _target_events(ordered, errors)
    job_slugs = {slugify(j.company) for j in jobs} - {None}
    views: dict[str, TargetView] = {}
    for state in found.values():
        linked = tuple(sorted(per for per, rec in people.items() if any(f.kind == "target" and f.id == state.id for f in rec.found_in)))
        linked_flags = [flags.get(per, PersonFlags()) for per in linked]
        activity = list(state.activity) + [f.last_activity for f in linked_flags if f.last_activity]
        record = targets.get(state.slug)
        for ev in (record.evidence if record is not None else ()):
            if afold.parse_ts(f"{ev.read_on}T00:00:00Z") is not None:
                activity.append(f"{ev.read_on}T00:00:00Z")
        last = max(activity) if activity else None
        conditions = {
            "dropped": state.decision == rules.DROPPED,
            "warm": any(f.label in ("connected", "replied") for f in linked_flags),
            "contacted": any(f.invited_at is not None for f in linked_flags),
            "opening-seen": state.slug in job_slugs,
            "approved": state.decision == rules.APPROVED,
            "candidate": True,
        }
        status = next(name for name in rules.STATUS_PRECEDENCE if conditions[name])
        stale = (state.decision != rules.DROPPED and last is not None
                 and current - afold.parse_ts(last) > timedelta(days=rules.TARGET_STALE_DAYS))
        views[state.slug] = TargetView(state.id, state.slug, state.decision, status, stale, last, linked)
    return TargetsFold(views, tuple(errors))
