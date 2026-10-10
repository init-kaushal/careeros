"""Career memory on disk: one markdown file with frontmatter per fact, plus sources.yaml."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from careeros.core import ids
from careeros.core.models import Issue, is_utc_timestamp
from careeros.core.workspace import (
    WorkspaceError, atomic_write_bytes, join_frontmatter, normalize_value, split_frontmatter,
)

CAREER = Path("career")
SOURCES_REL = CAREER / "sources.yaml"
KIND_DIR = {
    "experience": "experiences", "achievement": "achievements", "project": "projects", "skill": "skills",
    "education": "education", "certification": "certifications", "preference": "preferences",
    "goal": "goals", "constraint": "constraints", "evidence": "evidence",
}
KINDS = (*KIND_DIR, "identity")
ID_KIND = {kind: kind for kind in KINDS} | {"identity": "profile"}
STATUSES = ("claimed", "confirmed", "verified", "disputed", "retired")
ACTIVE = ("claimed", "confirmed", "verified")
ORIGINS = ("imported", "manual")
SOURCE_KINDS = ("resume", "user_statement", "document")
REQUIRED = {
    "experience": ("employer", "title", "start", "end"),
    "achievement": ("parent", "text"),
    "project": ("name", "text"),
    "skill": ("name",),
    "education": ("school", "degree"),
    "certification": ("name",),
    "preference": ("text",), "goal": ("text",), "constraint": ("text",),
    "identity": ("name",),
    "evidence": ("kind", "supports", "note"),
}
COMMON = ("id", "type", "schema", "status", "origin", "source", "created_at", "updated_at")
MEMORY_ID_PREFIXES = tuple(f"{ids.PREFIXES[ID_KIND[k]]}_" for k in KINDS)
_DATE = re.compile(r"^(\d{4}-(0[1-9]|1[0-2])|present)$")
_NULLABLE = {"parent"}


@dataclass
class Fact:
    fm: dict
    body: str
    path: Path

    @property
    def id(self) -> str:
        return str(self.fm.get("id"))

    @property
    def kind(self) -> str:
        return str(self.fm.get("type"))

    @property
    def status(self) -> str:
        return str(self.fm.get("status"))

    def get(self, key: str, default: object = None) -> object:
        return self.fm.get(key, default)


@dataclass
class Career:
    facts: list[Fact] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def active(self) -> list[Fact]:
        return [f for f in self.facts if f.status in ACTIVE]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fact_path(root: Path, kind: str, fact_id: str) -> Path:
    if kind == "identity":
        return Path(root) / CAREER / "identity.md"
    return Path(root) / CAREER / KIND_DIR[kind] / f"{fact_id}.md"


def heading(kind: str, fm: dict) -> str:
    text = {
        "experience": lambda: f"{fm.get('title')} — {fm.get('employer')}",
        "education": lambda: f"{fm.get('degree')} — {fm.get('school')}",
        "project": lambda: str(fm.get("name")),
        "skill": lambda: str(fm.get("name")),
        "certification": lambda: str(fm.get("name")),
        "identity": lambda: str(fm.get("name")),
        "evidence": lambda: str(fm.get("note")),
    }.get(kind, lambda: str(fm.get("text")))()
    return " ".join(text.split())[:100]


def new_frontmatter(kind: str, fact_id: str, fields: dict, source: dict, origin: str, now: str) -> dict:
    fm: dict = {
        "id": fact_id, "type": kind, "schema": 1, "status": "claimed", "origin": origin,
        **fields, "source": source, "created_at": now, "updated_at": now, "confirmed_at": None,
    }
    return fm


def render(fact: Fact) -> bytes:
    body = f"# {heading(fact.kind, fact.fm)}\n"
    return join_frontmatter(fact.fm, body).encode("utf-8")


def new_fact(root: Path, kind: str, fm: dict) -> Fact:
    return Fact(fm, "", fact_path(root, kind, str(fm["id"])))


def _files(root: Path) -> list[Path]:
    base = Path(root) / CAREER
    paths = [base / "identity.md"] if (base / "identity.md").is_file() else []
    for directory in KIND_DIR.values():
        paths.extend(sorted((base / directory).glob("*.md")))
    return paths


def known_ids(root: Path) -> set[str]:
    """Every memory id that has a file, read leniently (broken files still count by file name)."""
    found: set[str] = set()
    for path in _files(root):
        found.add(path.stem)
        try:
            fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, WorkspaceError):
            continue
        if fm and isinstance(fm.get("id"), str):
            found.add(fm["id"])
    return found


def load_sources(root: Path) -> dict[str, dict]:
    path = Path(root) / SOURCES_REL
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("sources", [])
        return {str(e["path"]): dict(e) for e in entries}
    except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError) as exc:
        raise WorkspaceError(f"career/sources.yaml could not be read: {exc}") from exc


def render_sources(entries: dict[str, dict]) -> bytes:
    data = {"sources": [entries[key] for key in sorted(entries)]}
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10_000).encode("utf-8")


def load_career(root: Path) -> Career:
    """Read every fact and report problems (MEM001-MEM008). Errors mean the memory cannot be trusted."""
    root = Path(root)
    career = Career()

    def add(severity: str, code: str, path: str, message: str, fix: str) -> None:
        career.issues.append(Issue(severity, code, path, message, fix))

    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        try:
            fm, body = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, WorkspaceError) as exc:
            add("error", "MEM001", rel, f"cannot be read: {exc}", "fix the file, or restore it from .careeros/backups or version control")
            continue
        if fm is None:
            add("error", "MEM001", rel, "has no frontmatter", "restore the --- frontmatter block, or restore the file from version control")
            continue
        career.facts.append(Fact({k: normalize_value(v) for k, v in fm.items()}, body, path))
    _validate(root, career, add)
    return career


def _validate(root: Path, career: Career, add) -> None:
    by_id: dict[str, Fact] = {}
    for fact in career.facts:
        rel = fact.path.relative_to(root).as_posix()
        fm = fact.fm
        kind = fm.get("type")
        if kind not in KINDS:
            add("error", "MEM004", rel, f"type {kind!r} is not a memory kind", "use one of: " + ", ".join(KINDS))
            continue
        missing = [k for k in (*COMMON, *REQUIRED[kind]) if k not in fm or (fm[k] is None and k not in _NULLABLE)]
        if missing:
            add("error", "MEM002", rel, f"missing: {', '.join(missing)}", "add the missing key(s) to the frontmatter")
        fact_id = fm.get("id")
        if not ids.is_valid_id(ID_KIND[kind], fact_id):
            add("error", "MEM002", rel, f"id {fact_id!r} is not a valid {kind} id", f"use {ids.PREFIXES[ID_KIND[kind]]}_ followed by 10 lowercase base32 characters")
        elif fact_id in by_id:
            add("error", "MEM002", rel, f"id {fact_id} is also used by {by_id[fact_id].path.relative_to(root).as_posix()}", "give one of the two facts a new id")
        else:
            by_id[str(fact_id)] = fact
            if kind != "identity" and fact.path.stem != fact_id:
                add("error", "MEM002", rel, f"the file name does not match its id {fact_id}", "rename the file to <id>.md")
        if fm.get("status") not in STATUSES:
            add("error", "MEM004", rel, f"status {fm.get('status')!r} is invalid", "use one of: " + ", ".join(STATUSES))
        if fm.get("origin") not in ORIGINS:
            add("error", "MEM004", rel, f"origin {fm.get('origin')!r} is invalid", "use imported or manual")
        for key in ("created_at", "updated_at", "confirmed_at"):
            if fm.get(key) is not None and not is_utc_timestamp(fm.get(key)):
                add("error", "MEM004", rel, f"{key} {fm.get(key)!r} is not a UTC ISO-8601 timestamp", "use the form 2026-10-06T09:15:00Z")
        source = fm.get("source")
        if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
            add("error", "MEM004", rel, "source is missing or has an invalid kind", "give the fact a source: resume, user_statement or document")
        elif source["kind"] == "user_statement" and not str(source.get("quote") or "").strip():
            add("error", "MEM004", rel, "a user_statement source needs the user's own words in quote", "add the quote")
        elif source["kind"] != "user_statement" and not source.get("path"):
            add("error", "MEM004", rel, f"a {source['kind']} source needs a path", "add the path")
        for key in ("start", "end"):
            if kind == "experience" and key in fm and not _DATE.match(str(fm[key])):
                add("error", "MEM004", rel, f"{key} {fm[key]!r} must be YYYY-MM or present", "use a value like 2024-01")
        if fm.get("status") in ("confirmed", "verified") and not fm.get("confirmed_at"):
            add("error", "MEM005", rel, f"{fm.get('status')} but confirmed_at is empty", "confirm the fact again with `careeros memory confirm`")
    for fact in career.facts:
        rel = fact.path.relative_to(root).as_posix()
        fm = fact.fm
        kind = fm.get("type")
        if kind == "achievement" and fm.get("parent") is not None and fm.get("parent") not in by_id:
            add("error", "MEM003", rel, f"parent {fm.get('parent')} does not exist", "restore the parent fact, or retire this one")
        if kind == "evidence":
            for target in fm.get("supports") or ():
                if target not in by_id:
                    add("error", "MEM003", rel, f"supports {target}, which does not exist", "restore that fact, or retire this evidence")
        if fm.get("status") == "verified":
            backed = any(
                e.kind == "evidence" and e.status in ACTIVE and fact.id in (e.fm.get("supports") or ())
                for e in career.facts
            )
            if not backed:
                add("error", "MEM005", rel, "verified, but no active evidence record supports it", "add evidence with `careeros memory add --kind evidence`, or dispute the fact")
        source = fm.get("source")
        if isinstance(source, dict) and fm.get("origin") == "imported" and source.get("kind") in ("resume", "document"):
            if source.get("path") and not (root / str(source["path"])).is_file():
                add("warning", "MEM007", rel, f"its source {source['path']} no longer exists", "restore the file, or retire the fact")
        if fm.get("stale"):
            add("warning", "MEM008", rel, "its source line is gone from the imported file", "retire the fact with `careeros memory retire`, or restore the line")
    try:
        sources = load_sources(root)
    except WorkspaceError as exc:
        add("error", "MEM001", SOURCES_REL.as_posix(), str(exc), "fix the file, or restore it from .careeros/backups")
        return
    for key, entry in sources.items():
        target = root / key
        if target.is_file() and sha256_hex(target.read_bytes()) != entry.get("sha256"):
            add("warning", "MEM006", key, f"{key} changed since it was imported", "run `careeros memory import` to review the differences")


class Transaction:
    """Remember what files looked like before, so a failed operation can put every one back."""

    def __init__(self) -> None:
        self._saved: dict[Path, bytes | None] = {}

    def write(self, path: Path, data: bytes) -> None:
        if path not in self._saved:
            self._saved[path] = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, data)

    def rollback(self) -> None:
        for path, original in reversed(list(self._saved.items())):
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, original)


def lexicon_for(root: Path, career: Career):
    """The bundled lexicon plus the user's additions and every technology named anywhere in the memory."""
    from careeros.core.memory.lexicon import load_lexicon

    names: list[str] = []
    for fact in career.facts:
        if fact.kind == "skill":
            names.append(str(fact.get("name") or ""))
        names.extend(str(t) for t in (fact.get("technologies") or ()) if isinstance(t, str))
    return load_lexicon(root).with_extra_techs(names)
