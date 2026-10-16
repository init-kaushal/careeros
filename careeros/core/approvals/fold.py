"""Item state folded from ledger events, and the gates built on it (spec sections 5 and 7). Pure.

Nothing here reads the clock or the filesystem: every function that needs the time takes `now` (a UTC timestamp string
such as `2026-10-15T09:00:00Z`), so one operation can read the clock once and use that value for every check.

* The fold is driven by `rules.TRANSITIONS`, over events ordered by `seq`. `approval.rejected` and
  `job.transition_rejected` are audit-only and are dropped first; an unknown `approval.*` type and any event that does
  not fit the item's history is reported in `ItemState.errors` (the source of `APR005`) and not applied.
* `expired` is computed, never stored: an approved item is expired when `now >= approved_at + 24h`. A released item keeps
  its original `approved_at`. An unreadable `approved_at` counts as expired.
* A proposal event names its kind and target in `action` as `propose <kind> for <target id>` and its file and action
  hash in `artifacts` as `[rel, "sha256:<hex>"]`. Contract: the proposal event is written ONLY through
  `proposal_event_fields`; `APR005` must fail when a proposal event does not parse, and `APR004`/`APR005` must
  cross-check the event's kind, target and hash against the item file.
* Contract for `clock_skewed`: every caller must pass `ItemState.last_ts`; the fold alone does not detect a clock that
  moved backwards.
* `fold_events` is the one call `APR005` is built on: `FoldResult.items` holds every item's `ItemState` (with its
  `errors`) and `FoldResult.unattributed` holds every `approval.*` problem that names no item (an unknown type, or any
  known type, with a missing or non-string entity). Audit-only events are ignored wherever they appear, entity or not.
  `fold_all` is the items part alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from careeros.core.approvals import rules
from careeros.core.approvals.model import ItemState, SequenceError, State

_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.ASCII)
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_PROPOSAL_ACTION = re.compile(r"propose (" + "|".join(rules.KINDS) + r") for (\S+)")
_HASH = re.compile(r"sha256:([0-9a-f]{64})")


@dataclass(frozen=True)
class Refusal:
    """`apply_transition` refused: a stable code from `rules.REFUSAL_CODES` and the state it was refused in."""
    code: str
    state: State



@dataclass(frozen=True)
class SubmissionStatus:
    consumed: tuple[str, ...]
    unresolved: tuple[tuple[str, State], ...]
    total: int = 0          # submit items for the job in any state; 0 means the job behaves as before approvals existed


# --- time ----------------------------------------------------------------------------------------------------------

def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not _TS.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, _TS_FORMAT)
    except ValueError:
        return None


def is_expired(approved_at: object, now: str) -> bool:
    """True when `now >= approved_at + 24h`. An unreadable `approved_at` or `now` is treated as expired."""
    approved, current = parse_ts(approved_at), parse_ts(now)
    if approved is None or current is None:
        return True
    return current >= approved + timedelta(seconds=rules.TTL_SECONDS)


def clock_skewed(approved_at: object, now: str, last_ts: object = None) -> bool:
    """True when `now` is earlier than the approval (or than the item's latest recorded event): the clock moved
    backwards or is wrong, and the caller must refuse with `clock_skew`. An unreadable `now` counts as skewed."""
    current = parse_ts(now)
    if current is None:
        return True
    return any(t is not None and current < t for t in (parse_ts(approved_at), parse_ts(last_ts)))


# --- the transition table --------------------------------------------------------------------------------------------

def apply_transition(state: State | str, operation: str) -> State | Refusal:
    """The state `operation` leads to from `state`, or a `Refusal` carrying the stable code. `state` is the EFFECTIVE
    state (an approved item past its expiry is `expired`). Raises ValueError for an unknown operation."""
    if operation not in rules.OPERATIONS:
        raise ValueError(f"unknown approval operation {operation!r}")
    state = State(state)
    new = rules.TRANSITIONS.get((state.value, operation))
    if new is not None:
        return State(new)
    return Refusal(rules.SPECIAL_REFUSALS.get((state.value, operation), rules.WRONG_STATE), state)


# --- events ----------------------------------------------------------------------------------------------------------

def proposal_event_fields(kind: str, target_id: str, rel: str, action_hash: str) -> dict:
    """The `action` and `artifacts` an `approval.proposed` event carries so the fold can read kind, target and hash."""
    return {"action": f"propose {kind} for {target_id}", "artifacts": [rel, f"sha256:{action_hash}"]}


def _order(indexed: tuple[int, dict]) -> tuple:
    index, event = indexed
    seq = event.get("seq")
    return (0, seq, index) if isinstance(seq, int) and not isinstance(seq, bool) else (1, 0, index)


def filter_events(events: Iterable[object]) -> tuple[list[dict], list[SequenceError]]:
    """The `approval.*` state events in `seq` order, with audit-only events dropped. An unknown `approval.*` type is
    returned as a `SequenceError` and not kept."""
    kept: list[tuple[int, dict]] = []
    errors: list[tuple[int, SequenceError]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        etype = event.get("type")
        if not isinstance(etype, str) or not etype.startswith(rules.EVENT_PREFIX) or etype in rules.AUDIT_ONLY_TYPES:
            continue
        entity = event.get("entity") if isinstance(event.get("entity"), str) else None
        if etype in rules.STATE_EVENTS:
            kept.append((index, event))
        else:
            errors.append((index, SequenceError(event.get("seq"), entity, f"unknown approval event type {etype!r}")))
    kept.sort(key=_order)
    errors.sort(key=lambda pair: _order((pair[0], {"seq": pair[1].seq})))
    return [e for _, e in kept], [e for _, e in errors]


def _proposal(event: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """(kind, target_id, rel, action_hash) read from a proposal event; any part may be None."""
    match = _PROPOSAL_ACTION.fullmatch(event.get("action") if isinstance(event.get("action"), str) else "")
    kind, target = (match[1], match[2]) if match else (None, None)
    arts = event.get("artifacts")
    rel = digest = None
    if isinstance(arts, list) and len(arts) == 2 and isinstance(arts[0], str) and isinstance(arts[1], str):
        found = _HASH.fullmatch(arts[1])
        if found:
            rel, digest = arts[0], found[1]
    return kind, target, rel, digest


def _fold_one(events: list[dict], extra: list[SequenceError], now: str) -> ItemState:
    raw: str | None = None
    errors = [e.message for e in extra]
    fields: dict = {}
    for event in events:
        etype, seq, at = event["type"], event.get("seq"), event.get("ts")
        if etype == rules.EV_PROPOSED:
            if raw is not None:
                errors.append(f"seq {seq}: a second {etype} for the same item")
                continue
            kind, target, rel, digest = _proposal(event)
            if kind is None:
                errors.append(f"seq {seq}: the proposal does not name a kind and target")
            if digest is None:
                errors.append(f"seq {seq}: the proposal does not carry the item path and action hash")
            raw = rules.PENDING
            fields.update(proposed_at=at, kind=kind, target_id=target, rel=rel, action_hash=digest)
        elif raw is None:
            errors.append(f"seq {seq}: {etype} before any proposal")
            continue
        else:
            new = rules.TRANSITIONS.get((raw, rules.OPERATION_FOR_EVENT[etype]))
            if new is None:
                errors.append(f"seq {seq}: {etype} is not legal for a {raw} item")
                continue
            raw = new
            if etype == rules.EV_APPROVED:
                fields["approved_at"] = at
            elif etype == rules.EV_STARTED:
                fields.update(started=True, started_at=at)
            elif etype == rules.EV_VOIDED:
                fields["void_reason"] = event.get("reason")
        fields["last_ts"] = at
    if raw is None:
        return ItemState(None, errors=tuple(errors))
    if raw == rules.APPROVED and is_expired(fields.get("approved_at"), now):
        raw = rules.EXPIRED
    return ItemState(State(raw), errors=tuple(errors), **fields)


@dataclass(frozen=True)
class FoldResult:
    items: dict[str, ItemState]
    unattributed: tuple[SequenceError, ...]


def fold_events(events: Iterable[object], now: str) -> FoldResult:
    """Every item that has any `approval.*` state event, in order of first appearance, as `{item id: ItemState}`
    (state=None plus errors when it has events but no proposal), and every error that names no item."""
    kept, errors = filter_events(events)
    grouped: dict[str, list[dict]] = {}
    unattributed = [e for e in errors if e.entity is None]
    for event in kept:
        if isinstance(event.get("entity"), str):
            grouped.setdefault(event["entity"], []).append(event)
        else:
            unattributed.append(SequenceError(event.get("seq"), None, f"{event['type']} names no item (entity {event.get('entity')!r})"))
    unattributed.sort(key=lambda e: _order((0, {"seq": e.seq})))
    extra: dict[str, list[SequenceError]] = {}
    for error in errors:
        if error.entity is not None:
            extra.setdefault(error.entity, []).append(error)
    for entity in extra:
        grouped.setdefault(entity, [])
    items = {entity: _fold_one(evs, extra.get(entity, []), now) for entity, evs in grouped.items()}
    return FoldResult(items, tuple(unattributed))


def fold_all(events: Iterable[object], now: str) -> dict[str, ItemState]:
    return fold_events(events, now).items


def fold_item(events: Iterable[object], item_id: str, now: str) -> ItemState:
    """The folded state of one item (`state=None` when it has no proposal event)."""
    mine = [e for e in events if isinstance(e, dict) and e.get("entity") == item_id]
    return fold_all(mine, now).get(item_id) or ItemState(None)


def live_items(events: Iterable[object], kind: str, target_id: str, now: str) -> tuple[tuple[str, ItemState], ...]:
    """Unresolved items (pending, approved and unexpired, in flight) of this kind for this target, oldest first."""
    return tuple((item_id, st) for item_id, st in fold_all(events, now).items()
                 if st.state is not None and st.state.value in rules.UNRESOLVED_STATES
                 and st.kind == kind and st.target_id == target_id)


# --- the APPLIED rule (set semantics: there is no "latest" item) --------------------------------------------------------

def submission_status(events: Iterable[object], job_id: str, now: str) -> SubmissionStatus:
    submits = [(item_id, st) for item_id, st in fold_all(events, now).items()
               if st.state is not None and st.kind == rules.APPLICATION_SUBMIT and st.target_id == job_id]
    return SubmissionStatus(
        consumed=tuple(i for i, st in submits if st.state is State.CONSUMED),
        unresolved=tuple((i, st.state) for i, st in submits if st.state.value in rules.UNRESOLVED_STATES),
        total=len(submits),
    )


def submission_refusal(status: SubmissionStatus) -> str | None:
    """None when the job may reach APPLIED (no submit items at all, or none unresolved and at least one consumed);
    otherwise `submission_unresolved`, which takes precedence, or `submission_not_consumed`."""
    if status.total == 0:
        return None
    if status.unresolved:
        return rules.SUBMISSION_UNRESOLVED
    if not status.consumed:
        return rules.SUBMISSION_NOT_CONSUMED
    return None
