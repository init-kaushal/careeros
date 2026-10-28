"""Shared person-event shapes and reads (outreach sub-project 4a). Pure: no clock, no filesystem, and it imports only
approvals modules, so approvals can use it without importing the outreach package.

* `outreach.person_added` is written ONLY through `person_added_fields`: `action` is `add person`, the person id is the
  event's `entity`, and `artifacts` is `[rel, "sha256:<hex>", "url:<canonical profile URL>"]`.
* The identity map (which `per_` id belongs to which canonical profile URL) is the union of `approval.proposed` url
  elements of invite and message items and `outreach.person_added` events, read in `seq` order, first seen wins. When the
  map is inconsistent `identity_problems` says how and callers must fail closed instead of picking a winner.
* Every reader returns `(value, errors)`: an event that cannot be read (not an object, no usable entity, a malformed url
  element) is reported in `errors`, never silently dropped. This is a recorded decision, not a security boundary.
"""

from __future__ import annotations

import re

from careeros.core import ids
from careeros.core.approvals import fold, item, render, rules

EV_PERSON_ADDED = "outreach.person_added"
EV_CLOSED = "outreach.closed"
PERSON_ADDED_ACTION = "add person"

_SHA = re.compile(r"[0-9a-f]{64}")
_SHA_ELEMENT = re.compile(r"sha256:[0-9a-f]{64}")
_EPOCH = "1970-01-01T00:00:00Z"        # the item fold needs a `now`; nothing read here depends on it


def person_added_fields(rel: str, sha: str, url: str) -> dict:
    """The `action` and `artifacts` an `outreach.person_added` event carries. ValueError for a bad part."""
    if not isinstance(rel, str) or not rel:
        raise ValueError("the record path must be a non-empty string")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError("sha must be 64 lower-case hex characters")
    if not isinstance(url, str) or item.canonical_profile_url(url) != url:
        raise ValueError("url must be a canonical LinkedIn profile URL")
    return {"action": PERSON_ADDED_ACTION, "artifacts": [rel, f"sha256:{sha}", f"url:{url}"]}


def parse_person_added(event: dict) -> tuple[str | None, str | None, str | None]:
    """`(person id, canonical url, None)` for a well-formed `outreach.person_added` event, otherwise
    `(id or None, None, problem)`."""
    seq = event.get("seq")
    entity = event.get("entity")
    per = entity if ids.is_valid_id("person", entity) else None
    if per is None:
        return None, None, f"seq {seq}: {EV_PERSON_ADDED} does not name a person id (entity {entity!r})"
    arts = event.get("artifacts")
    if not (isinstance(arts, list) and len(arts) == 3 and all(isinstance(a, str) for a in arts)):
        return per, None, f"seq {seq}: {EV_PERSON_ADDED} artifacts must be [rel, sha256, url]"
    if not _SHA_ELEMENT.fullmatch(arts[1]):
        return per, None, f"seq {seq}: {EV_PERSON_ADDED} has no sha256 element"
    if not arts[2].startswith("url:") or item.canonical_profile_url(arts[2][4:]) != arts[2][4:]:
        return per, None, f"seq {seq}: the {EV_PERSON_ADDED} url element is not `url:<canonical profile URL>`"
    return per, arts[2][4:], None


def _order(indexed: tuple[int, dict]) -> tuple:
    index, event = indexed
    seq = event.get("seq")
    return (0, seq, index) if isinstance(seq, int) and not isinstance(seq, bool) else (1, 0, index)


def _objects(events) -> tuple[list[dict], list[str]]:
    """The dict events in `seq` order, and one error per element that is not an object."""
    kept, errors = [], []
    for index, event in enumerate(events):
        if isinstance(event, dict):
            kept.append((index, event))
        else:
            errors.append(f"event {index} is not an object ({type(event).__name__})")
    kept.sort(key=_order)
    return [e for _, e in kept], errors


def _pairs(events: list[dict]) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """`([(person id, url)], person_added errors, proposal errors)`, pairs in `seq` order, from invite and message
    proposals and from `outreach.person_added`. A person-kind proposal with no readable url is a proposal error: that
    person must never be treated as unknown."""
    items = fold.fold_all(events, _EPOCH)
    pairs, errors, proposal_errors, proposed = [], [], [], set()
    for event in events:
        etype = event.get("type")
        if etype == rules.EV_PROPOSED and isinstance(event.get("entity"), str) and event["entity"] not in proposed:
            proposed.add(event["entity"])
            st = items.get(event["entity"])
            if st is not None and st.kind in (rules.LINKEDIN_INVITE, rules.LINKEDIN_MESSAGE):
                if st.url and ids.is_valid_id("person", st.target_id):
                    pairs.append((st.target_id, st.url))
                else:
                    proposal_errors.append(f"seq {event.get('seq')}: proposal {event['entity']} has no readable profile url "
                                           f"or person id")
        elif etype == EV_PERSON_ADDED:
            per, url, problem = parse_person_added(event)
            if problem:
                errors.append(problem)
            else:
                pairs.append((per, url))
    return pairs, errors, proposal_errors


def person_urls(events) -> tuple[dict[str, str], list[str]]:
    """`({canonical url: person id}, errors)` over proposals and `outreach.person_added` events; first seen wins."""
    objects, errors = _objects(events)
    pairs, more, bad = _pairs(objects)
    out: dict[str, str] = {}
    for per, url in pairs:
        out.setdefault(url, per)
    return out, errors + more + bad


def person_ids(events) -> tuple[dict[str, str], list[str]]:
    """`({person id: canonical url}, errors)`, the same union read from the other side; first seen wins."""
    objects, errors = _objects(events)
    pairs, more, bad = _pairs(objects)
    out: dict[str, str] = {}
    for per, url in pairs:
        out.setdefault(per, url)
    return out, errors + more + bad


def closed_people(events) -> tuple[set[str], list[str]]:
    """`({person ids with an outreach.closed event}, errors)`. This is the fail-closed gate: it counts a closed event
    even when its ts is unreadable, which `fold_people` leaves as `closed_at=None` plus an error. An event with no usable entity closes nobody and is
    reported; an entity that is not a person id still counts as closed and is reported."""
    objects, errors = _objects(events)
    closed: set[str] = set()
    for event in objects:
        if event.get("type") != EV_CLOSED:
            continue
        entity = event.get("entity")
        if not isinstance(entity, str) or not entity:
            errors.append(f"seq {event.get('seq')}: {EV_CLOSED} names no person (entity {entity!r})")
            continue
        closed.add(entity)
        if not ids.is_valid_id("person", entity):
            errors.append(f"seq {event.get('seq')}: {EV_CLOSED} entity {render.render_content(entity)!r} is not a person id")
    return closed, errors


def identity_problems(events) -> list[str]:
    """Inconsistencies in the identity map: one URL under two ids, or one id under two URLs, across proposals and
    `outreach.person_added` events, plus any invite or message proposal with no readable profile url. A malformed
    `outreach.person_added` is not a pair here; `person_urls` reports it."""
    objects, _ = _objects(events)
    pairs, _, bad = _pairs(objects)
    by_url: dict[str, list[str]] = {}
    by_id: dict[str, list[str]] = {}
    for per, url in pairs:
        if per not in by_url.setdefault(url, []):
            by_url[url].append(per)
        if url not in by_id.setdefault(per, []):
            by_id[per].append(url)
    problems = list(bad)
    problems += [f"profile URL {render.render_content(u)} is used under more than one person id: {', '.join(v)}"
                for u, v in sorted(by_url.items()) if len(v) > 1]
    problems += [f"person id {i} is used for more than one profile URL: {', '.join(render.render_content(u) for u in v)}"
                 for i, v in sorted(by_id.items()) if len(v) > 1]
    return problems
