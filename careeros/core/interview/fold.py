"""Round flags folded from ledger events (spec sections 5 and 6.2) and the writers of the event shapes. Pure.

Nothing here reads the clock or the filesystem; `fold_rounds` takes `now` (a UTC timestamp string). Inputs are plain
event dicts.

* Events are applied in `seq` order. `interview.rejected` is audit-only and ignored wherever it appears; events of other
  prefixes are ignored.
* Every problem is returned in `errors` (the source of `INT005`) and never dropped silently: an unknown `interview.*`
  type, an event that is not an object, has an unreadable `ts`, or does not name a `rnd_` id, a malformed event (an
  unsupported or missing `source` on `held`/`cancelled`, a missing reason on `cancelled`, a missing or invalid `job:` or
  `when:` element on `round_added`, a missing or invalid `when:` element on `rescheduled`), an event before the round's
  `round_added`, a second `round_added`, and a sequence the transition table refuses. Each problem event gives exactly one
  error.
* A malformed event is NEVER applied: it creates no flags entry, changes no flag and does not use up a once-only slot,
  so a later valid event of the same kind applies normally. A `round_added` whose `rel` or `sha256:` element is bad
  (the audit-trail part, not one of the spec's malformed cases) is reported and still applied.
* `recorded_when` is the `when:` of the latest APPLIED `round_added` or `rescheduled`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from careeros.core import ids
from careeros.core.approvals import fold as afold
from careeros.core.approvals.model import SequenceError
from careeros.core.interview import _common as c
from careeros.core.interview import rules
from careeros.core.interview.model import RoundFlags

_HEX = re.compile(r"[0-9a-f]{64}")
_SHA = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class Refusal:
    """A transition the table refuses: a stable code from `rules.REFUSAL_CODES` and the label it was refused in."""
    code: str
    state: str


@dataclass(frozen=True)
class RoundsFold:
    flags: dict[str, RoundFlags]
    errors: tuple[SequenceError, ...]


# --- the writers of the event shapes -------------------------------------------------------------------------------------

def _check_when(when: object) -> str:
    if c.parse_when(when) is None:
        raise ValueError(f"when must be an ISO date or a UTC datetime, not {when!r}")
    return when


def round_added_fields(rel: str, sha: str, job_id: str, when: str, *, stage: str | None = None) -> dict:
    """The `action` and `artifacts` an `interview.round_added` event carries: `[rel, "sha256:<hex>", "job:<job id>",
    "when:<scheduled_for>"]`. The action names the stage (when given) and the date for humans; nothing reads it."""
    if not isinstance(rel, str) or not rel:
        raise ValueError("the record path must be a non-empty string")
    if not isinstance(sha, str) or not _HEX.fullmatch(sha):
        raise ValueError("sha must be 64 lower-case hex characters")
    if not ids.is_valid_id("job", job_id):
        raise ValueError(f"{job_id!r} is not a job id")
    _check_when(when)
    if stage is not None and stage not in rules.STAGES:
        raise ValueError(f"stage must be one of {', '.join(rules.STAGES)}")
    what = f"{stage} round" if stage else "round"
    return {"action": f"add {what} for {job_id} on {when}",
            "artifacts": [rel, f"sha256:{sha}", f"job:{job_id}", f"when:{when}"]}


def rescheduled_fields(when: str) -> dict:
    """The `action` and `artifacts` an `interview.rescheduled` event carries: `["when:<new scheduled_for>"]`."""
    _check_when(when)
    return {"action": f"reschedule to {when}", "artifacts": [f"when:{when}"]}


def rejected_fields(operation: str, entity: str, code: str, message: str) -> dict:
    """The `action` and `reason` of an audit-only `interview.rejected` for an EXISTING round (callers dedupe on the
    entity's latest event of any type and append nothing for an unknown entity)."""
    if not ids.is_valid_id("round", entity):
        raise ValueError(f"{entity!r} is not a round id")
    if code not in rules.REFUSAL_CODES:
        raise ValueError(f"unknown refusal code {code!r}")
    return {"action": f"rejected {operation} of {entity}", "reason": f"{code}: {message}"}


# --- transitions ---------------------------------------------------------------------------------------------------------

def apply_event(flags: RoundFlags, etype: str, ts: str, *, reason: str | None = None, when: str | None = None) -> RoundFlags | Refusal:
    """`flags` with `etype` (held, cancelled, rescheduled or debriefed) applied at `ts`, or a `wrong_state` `Refusal`
    (`state` is the current label). `reason` is kept for `cancelled`; `when` is required for `rescheduled`. Raises
    ValueError for any other event type."""
    if etype not in (rules.EV_HELD, rules.EV_CANCELLED, rules.EV_RESCHEDULED, rules.EV_DEBRIEFED):
        raise ValueError(f"{etype!r} is not a round transition")
    if etype == rules.EV_RESCHEDULED:
        _check_when(when)
    label = flags.label
    if (label, etype) not in rules.TRANSITIONS:
        return Refusal(rules.WRONG_STATE, label)
    if etype == rules.EV_RESCHEDULED:
        return replace(flags, recorded_when=when)
    changes: dict = {rules.FLAG_FOR_EVENT[etype]: ts}
    if etype == rules.EV_CANCELLED:
        changes["cancel_reason"] = reason
    return replace(flags, **changes)


# --- the fold ------------------------------------------------------------------------------------------------------------

def _order(indexed: tuple[int, dict]) -> tuple:
    index, event = indexed
    seq = event.get("seq")
    return (0, seq, index) if isinstance(seq, int) and not isinstance(seq, bool) else (1, 0, index)


def _when_element(value: object) -> str | None:
    if isinstance(value, str) and value.startswith("when:") and c.parse_when(value[5:]) is not None:
        return value[5:]
    return None


def round_added_shape(event: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """(job, when, malformed problem, reported-only problem) of an `interview.round_added` event: `job` and `when` are
    None when `malformed problem` is set. Public: the validation section (INT004, one id under two jobs) uses it."""
    arts = event.get("artifacts")
    if not isinstance(arts, list) or len(arts) != 4:
        return None, None, "artifacts must be [rel, sha256:<hex>, job:<job id>, when:<scheduled_for>]", None
    job_el, when = arts[2], _when_element(arts[3])
    job = job_el[4:] if isinstance(job_el, str) and job_el.startswith("job:") else None
    if not ids.is_valid_id("job", job):
        return None, None, f"the job element {job_el!r} must be job:<job id>", None
    if when is None:
        return None, None, f"the when element {arts[3]!r} must be when:<ISO date or UTC datetime>", None
    audit = None
    if not (isinstance(arts[0], str) and arts[0] and isinstance(arts[1], str) and _SHA.fullmatch(arts[1])):
        audit = "the rel and sha256 elements must be a path and sha256:<hex>"
    return job, when, None, audit


def _malformed(event: dict, etype: str) -> str | None:
    if etype in rules.SOURCED_EVENTS and (not isinstance(event.get("source"), str) or event["source"] not in rules.SOURCES):
        return f"source must be one of {', '.join(rules.SOURCES)}"
    if etype == rules.EV_CANCELLED:
        reason = event.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return "a cancelled event needs a reason"
    if etype == rules.EV_RESCHEDULED:
        arts = event.get("artifacts")
        if not (isinstance(arts, list) and len(arts) == 1 and _when_element(arts[0]) is not None):
            return "artifacts must be [when:<ISO date or UTC datetime>]"
    return None


def fold_rounds(events: Iterable[object], now: str) -> RoundsFold:
    """Flags for every round with an applied `round_added`, plus every problem found (the source of `INT005`). Raises
    ValueError when `now` is unreadable."""
    if afold.parse_ts(now) is None:
        raise ValueError(f"unreadable now {now!r}")
    kept, errors = [], []
    for index, event in enumerate(events):
        if isinstance(event, dict):
            kept.append((index, event))
        else:
            errors.append(SequenceError(None, None, f"event {index} is not an object ({type(event).__name__})"))
    kept.sort(key=_order)
    flags: dict[str, RoundFlags] = {}
    for _, event in kept:
        etype = event.get("type")
        if not isinstance(etype, str) or not etype.startswith(rules.EVENT_PREFIX) or etype in rules.AUDIT_ONLY_TYPES:
            continue
        seq = event.get("seq")
        entity = event.get("entity") if isinstance(event.get("entity"), str) else None

        def report(message: str) -> None:
            errors.append(SequenceError(seq, entity, f"{etype}: {message}"))

        if etype not in rules.STATE_EVENTS:
            report(f"unknown interview event type {etype!r}")
            continue
        if not ids.is_valid_id("round", entity):
            report(f"does not name a round id (entity {event.get('entity')!r})")
            continue
        at = event.get("ts")
        if afold.parse_ts(at) is None:
            report("has an unreadable ts")
            continue
        if etype == rules.EV_ROUND_ADDED:
            job, when, problem, audit = round_added_shape(event)
            if problem is not None:
                report(problem)
            elif entity in flags:
                report("a second round_added for the same round")
            else:
                if audit is not None:
                    report(audit)
                flags[entity] = RoundFlags(job=job, added_at=at, recorded_when=when)
            continue
        problem = _malformed(event, etype)
        if problem is not None:
            report(problem)
            continue
        current = flags.get(entity)
        if current is None:
            report("before any round_added")
            continue
        when = event["artifacts"][0][5:] if etype == rules.EV_RESCHEDULED else None
        result = apply_event(current, etype, at, reason=event.get("reason"), when=when)
        if isinstance(result, Refusal):
            report(f"is not legal for a {result.state} round")
        else:
            flags[entity] = result
    return RoundsFold(flags, tuple(errors))
