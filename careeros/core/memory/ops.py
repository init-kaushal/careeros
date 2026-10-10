"""Changing the career memory: add, update, confirm, verify, dispute and retire facts.

Every operation runs under the workspace lock, writes atomically, appends its ledger event, and puts
the files back if the ledger append fails.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from careeros.core import ids, ledger, models
from careeros.core.memory import facets, store
from careeros.core.memory.store import ACTIVE, Career, Fact, Transaction
from careeros.core.workspace import WorkspaceError, WorkspaceLock, ensure_writable

Confirm = Callable[[str], bool]
EDITABLE = {
    "experience": ("employer", "title", "start", "end", "technologies", "location"),
    "achievement": ("text",),
    "project": ("name", "text", "technologies"),
    "skill": ("name", "category"),
    "education": ("school", "degree", "field", "end"),
    "certification": ("name", "issuer", "year"),
    "preference": ("text",), "goal": ("text",), "constraint": ("text",),
    "identity": ("name", "location", "headlines"),
    "evidence": ("note",),
}
_LIST_FIELDS = {"technologies", "headlines", "supports"}
_DATE = re.compile(r"^(\d{4}-(0[1-9]|1[0-2])|present)$")


class MemoryOpError(WorkspaceError):
    """The memory cannot be changed that way."""


def open_career(root: Path) -> Career:
    ensure_writable(root)
    career = store.load_career(root)
    if career.errors:
        first = career.errors[0]
        raise MemoryOpError(f"the career memory has errors ({first.code} {first.path}: {first.message}); run `careeros validate`")
    return career


def _as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(v) for v in (value or ())]


def _prepare(kind: str, fields: dict, career: Career, lexicon) -> dict:
    fields = dict(fields)
    for key in _LIST_FIELDS & set(fields):
        fields[key] = _as_list(fields[key])
    if kind == "experience":
        fields.setdefault("technologies", [])
    required = store.REQUIRED[kind]
    missing = [k for k in required if k not in fields or (fields[k] in (None, "", []) and k != "parent")]
    if missing:
        raise MemoryOpError(f"a {kind} needs: {', '.join(missing)}")
    for key in ("start", "end"):
        if kind == "experience" and not _DATE.match(str(fields[key])):
            raise MemoryOpError(f"{key} must be YYYY-MM or present, not {fields[key]!r}")
    if kind in ("achievement", "project", "education") and fields.get("text"):
        fields.update(facets.derive_facets(str(fields["text"]), lexicon))
    if kind == "achievement":
        parent = fields.get("parent")
        if parent is None:
            if fields.get("section") != "summary":
                raise MemoryOpError("an achievement needs a parent experience or project (--set parent=<id>)")
        elif parent not in career.by_id() or career.by_id()[parent].kind not in ("experience", "project"):
            raise MemoryOpError(f"parent {parent} is not an experience or project in the memory")
    if kind == "evidence":
        if fields["kind"] not in ("document", "link"):
            raise MemoryOpError("evidence kind must be document or link")
        if not (fields.get("path") or fields.get("url")):
            raise MemoryOpError("evidence needs a --set path=... or --set url=...")
        for target in fields["supports"]:
            if target not in career.by_id():
                raise MemoryOpError(f"supports {target}, which is not in the memory")
    return fields


def event_spec(fact_id: str, event_type: str, action: str, *, actor: str, prev: str | None = None,
           new: str | None = None, reason: str | None = None, artifacts: list[str] | None = None) -> dict:
    spec = {"type": event_type, "actor": actor, "entity": fact_id, "prev_state": prev, "new_state": new,
            "action": action, "source": "cli"}
    if reason:
        spec["reason"] = reason
    if artifacts:
        spec["artifacts"] = artifacts
    return spec


def _commit(root: Path, tx: Transaction, specs: list[dict]) -> None:
    try:
        ledger.append_events(root, specs)
    except BaseException:
        tx.rollback()
        raise


def add_fact(root: Path, kind: str, fields: dict, *, quote: str | None = None, actor: str = "user",
             source: dict | None = None, origin: str = "manual") -> Fact:
    if kind not in store.KINDS:
        raise MemoryOpError(f"unknown kind {kind!r}; use one of: {', '.join(store.KINDS)}")
    root = Path(root)
    with WorkspaceLock(root):
        career = open_career(root)
        if kind == "identity" and any(f.kind == "identity" for f in career.facts):
            raise MemoryOpError("an identity already exists; change it with `careeros memory update`")
        fields = _prepare(kind, fields, career, store.lexicon_for(root, career))
        if source is None:
            if kind == "evidence":
                source = ({"kind": "document", "path": fields["path"]} if fields.get("path")
                          else {"kind": "user_statement", "quote": fields["note"]})
            elif quote and quote.strip():
                source = {"kind": "user_statement", "quote": quote.strip()}
            else:
                raise MemoryOpError("--quote is required: give the user's own words for this fact")
        fact_id = ids.new_id(store.ID_KIND[kind], career.by_id())
        fm = store.new_frontmatter(kind, fact_id, fields, source, origin, models.utc_now())
        fact = store.new_fact(root, kind, fm)
        tx = Transaction()
        tx.write(fact.path, store.render(fact))
        _commit(root, tx, [event_spec(fact_id, "memory.fact_added", f"added {kind}: {store.heading(kind, fm)}",
                                  actor=actor, new="claimed")])
        return fact


def _find(career: Career, fact_id: str) -> Fact:
    fact = career.by_id().get(fact_id)
    if fact is None:
        raise MemoryOpError(f"no fact with id {fact_id}")
    return fact


def _decline(root: Path, fact: Fact, what: str, actor: str) -> None:
    ledger.append_events(root, [event_spec(fact.id, "memory.declined", f"declined to {what} {fact.id}", actor=actor)])


def _need_confirm(fact: Fact, what: str, confirm: Confirm | None) -> Confirm:
    if confirm is None:
        raise MemoryOpError(f"{what} {fact.id} records the user's own decision and needs an interactive terminal")
    return confirm


def update_fact(root: Path, fact_id: str, changes: dict, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    root = Path(root)
    with WorkspaceLock(root):
        career = open_career(root)
        fact = _find(career, fact_id)
        if fact.status == "retired":
            raise MemoryOpError(f"{fact_id} is retired and cannot be changed")
        unknown = set(changes) - set(EDITABLE[fact.kind])
        if unknown or not changes:
            raise MemoryOpError(f"a {fact.kind} can change: {', '.join(EDITABLE[fact.kind])}")
        prev = fact.status
        if prev in ("confirmed", "verified"):
            ask = _need_confirm(fact, "changing", confirm)
            if not ask(f"{fact_id} is {prev}. Changing it resets it to claimed so you review it again. Continue?"):
                _decline(root, fact, "change", actor)
                return None
        fm = dict(fact.fm)
        for key, value in changes.items():
            fm[key] = _as_list(value) if key in _LIST_FIELDS else value
        if fact.kind == "experience":
            for key in ("start", "end"):
                if not _DATE.match(str(fm[key])):
                    raise MemoryOpError(f"{key} must be YYYY-MM or present, not {fm[key]!r}")
        if "text" in changes and fact.kind in ("achievement", "project"):
            fm.update(facets.derive_facets(str(fm["text"]), store.lexicon_for(root, career)))
        fm["origin"] = "manual"
        fm["status"], fm["confirmed_at"], fm["updated_at"] = "claimed", None, models.utc_now()
        updated = Fact(fm, fact.body, fact.path)
        tx = Transaction()
        tx.write(updated.path, store.render(updated))
        _commit(root, tx, [event_spec(fact_id, "memory.fact_updated", f"updated {fact.kind}: {', '.join(changes)}",
                                  actor=actor, prev=prev, new="claimed")])
        return updated


def _set_status(root: Path, fact_id: str, new: str, event_type: str, *, actor: str, allowed: tuple[str, ...],
                confirm: Confirm | None, needs_human: bool, reason: str | None = None,
                artifacts: list[str] | None = None, check=None) -> Fact | None:
    root = Path(root)
    with WorkspaceLock(root):
        career = open_career(root)
        fact = _find(career, fact_id)
        if fact.status not in allowed:
            raise MemoryOpError(f"{fact_id} is {fact.status}; this needs it to be {' or '.join(allowed)}")
        if check:
            check(career, fact)
        prev = fact.status
        if needs_human or prev != "claimed":
            ask = _need_confirm(fact, new, confirm)
            if not ask(f"Mark {fact_id} ({store.heading(fact.kind, fact.fm)}) as {new}?"):
                _decline(root, fact, new, actor)
                return None
        fm = dict(fact.fm)
        fm["status"], fm["updated_at"] = new, models.utc_now()
        if new in ("confirmed", "verified"):
            fm["confirmed_at"] = fm.get("confirmed_at") or fm["updated_at"]
        if new in ("disputed", "retired"):
            fm["confirmed_at"] = None
        updated = Fact(fm, fact.body, fact.path)
        tx = Transaction()
        tx.write(updated.path, store.render(updated))
        _commit(root, tx, [event_spec(fact_id, event_type, f"{new} {fact_id}", actor=actor, prev=prev, new=new,
                                  reason=reason, artifacts=artifacts)])
        return updated


def confirm_fact(root: Path, fact_id: str, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    return _set_status(root, fact_id, "confirmed", "memory.fact_confirmed", actor=actor, allowed=("claimed",),
                       confirm=confirm, needs_human=True)


def verify_fact(root: Path, fact_id: str, evidence_id: str, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    def check(career: Career, fact: Fact) -> None:
        evidence = career.by_id().get(evidence_id)
        if evidence is None or evidence.kind != "evidence" or evidence.status not in ACTIVE:
            raise MemoryOpError(f"{evidence_id} is not an active evidence record")
        if fact.id not in (evidence.get("supports") or ()):
            raise MemoryOpError(f"{evidence_id} does not list {fact.id} under supports")

    return _set_status(root, fact_id, "verified", "memory.fact_verified", actor=actor, allowed=("confirmed",),
                       confirm=confirm, needs_human=True, artifacts=[evidence_id], check=check)


def dispute_fact(root: Path, fact_id: str, reason: str, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    if not reason.strip():
        raise MemoryOpError("a reason is required to dispute a fact")
    return _set_status(root, fact_id, "disputed", "memory.fact_disputed", actor=actor,
                       allowed=("claimed", "confirmed", "verified"), confirm=confirm, needs_human=False, reason=reason.strip())


def retire_fact(root: Path, fact_id: str, reason: str, *, actor: str = "user", confirm: Confirm | None = None) -> Fact | None:
    if not reason.strip():
        raise MemoryOpError("a reason is required to retire a fact")
    return _set_status(root, fact_id, "retired", "memory.fact_retired", actor=actor,
                       allowed=("claimed", "confirmed", "verified", "disputed"), confirm=confirm,
                       needs_human=False, reason=reason.strip())
