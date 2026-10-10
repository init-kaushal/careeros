"""Import resume.md into the career memory: a deterministic parse, a reviewable diff, an atomic apply.

The importer only ever creates `claimed` facts. It never overwrites a confirmed fact, never touches a
manual one, and never deletes anything: a line that disappeared from the resume marks its fact `stale`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from careeros.core import backup as backup_helpers
from careeros.core import ids, ledger, models, versions
from careeros.core.memory import facets, store
from careeros.core.memory.lexicon import Lexicon
from careeros.core.memory.normalize import norm_text, split_sentences
from careeros.core.memory.ops import MemoryOpError, event_spec, open_career
from careeros.core.memory.store import Fact, Transaction
from careeros.core.workspace import WorkspaceLock

SECTION_NAMES = {
    "summary": ("summary", "profile", "about", "about me", "objective"),
    "experience": ("experience", "work experience", "professional experience", "employment", "employment history"),
    "education": ("education",),
    "projects": ("projects", "personal projects", "open source"),
    "skills": ("skills", "technical skills", "technologies"),
    "certifications": ("certifications", "certificates", "certification"),
}
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_POINT = (r"(?:(?P<{p}mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(?P<{p}y>\d{{4}})"
          r"|(?P<{p}m>\d{{1,2}})/(?P<{p}yy>\d{{4}})|(?P<{p}yr>\d{{4}}))")
_RANGE = re.compile(
    _POINT.format(p="a") + r"\s*[-–—]\s*(?:" + _POINT.format(p="b") + r"|(?P<present>present|current|now)\b)", re.I)
_TECH_LINE = re.compile(r"^(?:tech(?:nologies)?(?:\s+stack)?|stack)\s*:\s*(.+)$", re.I)
_BOLD = re.compile(r"^\*\*(.+?)\*\*\s*[-–—,:]?\s*(.*)$")
_CATEGORY = re.compile(r"^(?:\*\*(?P<b>.+?)\*\*:?|(?P<p>[A-Za-z][\w &/+.-]{0,40}):)\s+(?P<items>.+)$")
_MIN_OVERLAP = 0.6


@dataclass
class Candidate:
    kind: str
    key: str
    fields: dict
    line: int
    line_sha: str
    parent_key: str | None = None


@dataclass
class ParseResult:
    candidates: list[Candidate] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _point_ym(match: re.Match, prefix: str, edge: str) -> str:
    if match.group(prefix + "mon"):
        return f"{int(match.group(prefix + 'y')):04d}-{_MONTHS[match.group(prefix + 'mon').lower()[:3]]:02d}"
    if match.group(prefix + "m"):
        return f"{int(match.group(prefix + 'yy')):04d}-{int(match.group(prefix + 'm')):02d}"
    year = match.group(prefix + "yr")
    return f"{year}-01" if edge == "start" else f"{year}-12"


def _date_range(line: str) -> tuple[str, str] | None:
    match = _RANGE.search(line)
    if not match:
        return None
    end = "present" if match.group("present") else _point_ym(match, "b", "end")
    return _point_ym(match, "a", "start"), end


def _last_point(text: str) -> str | None:
    found = list(re.finditer(_POINT.format(p="e"), text, re.I))
    return _point_ym(found[-1], "e", "end") if found else None


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        depth += char == "("
        depth -= char == ")"
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    parts.append(current.strip())
    return [p for p in parts if p]


def _strip_bullet(line: str) -> str:
    return re.sub(r"^\s*[-*+•]\s+", "", line).strip()


def _sha(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _hash12(text: str) -> str:
    return hashlib.sha1(norm_text(text).encode("utf-8")).hexdigest()[:12]


def _section_of(title: str) -> str | None:
    folded = norm_text(title)
    return next((kind for kind, names in SECTION_NAMES.items() if folded in names), None)


class _Parser:
    def __init__(self, lexicon: Lexicon) -> None:
        self.lx = lexicon
        self.out = ParseResult()
        self.keys: set[str] = set()
        self.exp: Candidate | None = None
        self.edu: Candidate | None = None
        self.prj: Candidate | None = None
        self.prj_text_set = False
        self.dated = False

    def add(self, kind: str, key: str, fields: dict, n: int, raw: str, parent_key: str | None = None) -> Candidate | None:
        if key in self.keys:
            self.out.skipped.append(f"line {n}: duplicate of an earlier line, ignored")
            return None
        self.keys.add(key)
        cand = Candidate(kind, key, fields, n, _sha(raw), parent_key)
        self.out.candidates.append(cand)
        return cand

    def achievement(self, text: str, n: int, raw: str, section: str, parent: Candidate | None) -> None:
        fields = {"text": text, **facets.derive_facets(text, self.lx), "section": section}
        parent_key = parent.key if parent else None
        self.add("achievement", f"ach|{parent_key or 'summary'}|{_hash12(text)}", fields, n, raw, parent_key)

    def finish_experience(self) -> None:
        if self.exp is not None and not self.dated:
            self.out.errors.append(
                f"line {self.exp.line}: experience {self.exp.fields['employer']!r} has no date range "
                f"(expected a line such as 'Jan 2024 - Present')")
        self.exp, self.dated = None, False

    def finish_education(self) -> None:
        if self.edu is not None and "degree" not in self.edu.fields:
            self.out.errors.append(f"line {self.edu.line}: education {self.edu.fields['school']!r} has no degree line under it")
        self.edu = None

    def feed(self, section: str, n: int, line: str) -> None:
        getattr(self, f"_{section}")(n, line)

    def _summary(self, n: int, line: str) -> None:
        for sentence in split_sentences(_strip_bullet(line)):
            self.achievement(sentence, n, sentence, "summary", None)

    def _experience(self, n: int, line: str) -> None:
        heading = re.match(r"^###\s+(.+)$", line)
        if heading:
            self.finish_experience()
            if "|" not in heading.group(1):
                self.out.errors.append(f"line {n}: expected '### Employer | Title', found {line.strip()!r}")
                return
            employer, title = (part.strip() for part in heading.group(1).split("|", 1))
            fields = {"employer": employer, "title": title, "technologies": []}
            self.exp = self.add("experience", f"exp|{norm_text(employer)}|{norm_text(title)}", fields, n, line)
            return
        if self.exp is None:
            self.out.errors.append(f"line {n}: expected '### Employer | Title' before this line, found {line.strip()!r}")
            return
        if not self.dated:
            dates = _date_range(line)
            if dates:
                self.exp.fields["start"], self.exp.fields["end"] = dates
                self.dated = True
                return
        text = _strip_bullet(line)
        tech = _TECH_LINE.match(text)
        if tech:
            names = _split_top_level(tech.group(1))
            self.exp.fields["technologies"] = [self.lx.canonical_tech(name) or name for name in names]
            return
        self.achievement(text, n, text, "experience", self.exp)

    def _education(self, n: int, line: str) -> None:
        bold = _BOLD.match(line.strip())
        if bold:
            self.finish_education()
            school, tail = bold.group(1).strip().rstrip(":"), bold.group(2)
            end = _last_point(tail)
            if end is None:
                self.out.errors.append(f"line {n}: education {school!r} needs a date after the name (for example '- Jun 2020')")
                return
            self.edu = self.add("education", f"edu|{norm_text(school)}", {"school": school, "end": end}, n, line)
            return
        if self.edu is None or "degree" in self.edu.fields:
            self.out.errors.append(f"line {n}: expected '**School** - Month YYYY' then a degree line, found {line.strip()!r}")
            return
        degree, _, rest = line.partition("|")
        self.edu.fields["degree"] = degree.strip()
        if rest.strip():
            self.edu.fields["text"] = rest.strip()
            self.edu.fields.update(facets.derive_facets(rest, self.lx))
        self.edu.key = f"edu|{norm_text(self.edu.fields['school'])}|{norm_text(degree)}"

    def _projects(self, n: int, line: str) -> None:
        heading = re.match(r"^###\s+(.+)$", line)
        bold = _BOLD.match(line.strip()) if not line.lstrip().startswith(("-", "*+")) else None
        if heading or bold:
            name = (heading.group(1) if heading else bold.group(1)).strip()
            fields = {"name": name, "text": name, **facets.derive_facets(name, self.lx)}
            self.prj = self.add("project", f"prj|{norm_text(name)}", fields, n, line)
            self.prj_text_set = False
            return
        text = _strip_bullet(line)
        is_bullet = bool(re.match(r"^\s*[-*+•]\s+", line))
        if self.prj is None:
            if not is_bullet:
                self.out.errors.append(f"line {n}: expected a project heading ('### Name' or '**Name**'), found {line.strip()!r}")
                return
            fields = {"name": text[:60], "text": text, **facets.derive_facets(text, self.lx)}
            self.add("project", f"prj|{norm_text(text[:60])}", fields, n, line)
            return
        if not is_bullet and not self.prj_text_set:
            self.prj.fields["text"] = text
            self.prj.fields.update(facets.derive_facets(f"{self.prj.fields['name']}. {text}", self.lx))
            self.prj_text_set = True
            return
        self.achievement(text, n, text, "projects", self.prj)

    def _skills(self, n: int, line: str) -> None:
        text = _strip_bullet(line)
        match = _CATEGORY.match(text)
        category = (match.group("b") or match.group("p")).strip().rstrip(":") if match else None
        items = match.group("items") if match else text
        for item in _split_top_level(items):
            inner = re.match(r"^(.+?)\s*\((.+)\)$", item)
            names = [inner.group(1).strip(), *_split_top_level(inner.group(2))] if inner else [item]
            for name in names:
                self.add("skill", f"skl|{norm_text(name)}", {"name": name, "category": category}, n, name)

    def _certifications(self, n: int, line: str) -> None:
        text = _strip_bullet(line)
        year = re.search(r"\b(19|20)\d{2}\b", text)
        name = re.sub(r"[\s,(–—-]*\(?\b(?:19|20)\d{2}\b\)?\s*$", "", text).strip(" ,") if year else text
        fields = {"name": name}
        if year:
            fields["year"] = year.group(0)
        self.add("certification", f"crt|{norm_text(name)}", fields, n, text)


def parse_resume(text: str, lexicon: Lexicon) -> ParseResult:
    """Turn resume.md into candidate facts. Anything the grammar cannot place is an error or is reported."""
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    parser = _Parser(lexicon)
    section: str | None = None
    skipped: dict[str, list[int]] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line.strip():
            continue
        heading = re.match(r"^##\s+(.+?)\s*#*$", line)
        if heading:
            parser.finish_experience()
            parser.finish_education()
            parser.prj = None
            section = _section_of(heading.group(1))
            if section is None:
                skipped.setdefault(f"section '{heading.group(1).strip()}'", []).append(n)
            continue
        if re.match(r"^#\s", line):
            continue
        if section is None:
            skipped.setdefault("preamble" if not skipped else next(reversed(skipped)), []).append(n)
            continue
        parser.feed(section, n, line)
    parser.finish_experience()
    parser.finish_education()
    for name, lines in skipped.items():
        parser.out.skipped.append(f"{name} (line {lines[0]}, {len(lines)} line(s)) was not imported")
    return parser.out


# --- diff -----------------------------------------------------------------------------------

@dataclass
class Change:
    result: str                      # unchanged | added | changed | removed
    action: str                      # none | create | update | unstale | mark_stale | skip_reviewed | skip_manual
    candidate: Candidate | None = None
    fact: Fact | None = None

    @property
    def label(self) -> str:
        if self.candidate is not None:
            return f"{self.candidate.kind:<13} {store.heading(self.candidate.kind, self.candidate.fields)}"
        return f"{self.fact.kind:<13} {store.heading(self.fact.kind, self.fact.fm)}"


@dataclass
class ImportPlan:
    source_rel: str
    source_sha: str
    changes: list[Change] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def count(self, result: str) -> int:
        return sum(1 for c in self.changes if c.result == result)

    @property
    def pending_review(self) -> int:
        return sum(1 for c in self.changes if c.action == "skip_reviewed")

    @property
    def actionable(self) -> bool:
        return any(c.action in ("create", "update", "unstale", "mark_stale") for c in self.changes)


def _words(text: str) -> set[str]:
    return set(norm_text(text).split())


def _jaccard(a: str, b: str) -> float:
    left, right = _words(a), _words(b)
    return len(left & right) / len(left | right) if left | right else 0.0


def _same(candidate: Candidate, fact: Fact) -> bool:
    if candidate.kind == "achievement":
        return True  # the key already contains the text hash
    return all(fact.get(k) == v for k, v in candidate.fields.items())


def _decide(result: str, fact: Fact | None) -> str:
    if result == "added":
        return "create"
    if fact is not None and fact.get("origin") == "manual":
        return "skip_manual" if result in ("changed", "removed") else "none"
    if result == "unchanged":
        return "unstale" if fact is not None and fact.get("stale") else "none"
    if result == "changed":
        return "update" if fact.status == "claimed" else "skip_reviewed"
    return "none" if fact.get("stale") else "mark_stale"


def plan_import(root: Path, source_rel: str = "resume.md") -> ImportPlan:
    root = Path(root)
    path = root / source_rel
    if not path.is_file():
        raise MemoryOpError(f"{source_rel} does not exist in this workspace")
    data = path.read_bytes()
    plan = ImportPlan(source_rel, store.sha256_hex(data))
    career = store.load_career(root)
    if career.errors:
        first = career.errors[0]
        raise MemoryOpError(f"the career memory has errors ({first.code} {first.path}: {first.message}); run `careeros validate`")
    parsed = parse_resume(data.decode("utf-8"), store.lexicon_for(root, career))
    plan.skipped, plan.errors = parsed.skipped, parsed.errors
    if parsed.errors:
        return plan
    by_id = career.by_id()
    existing = [
        f for f in career.facts
        if f.get("import_key") and isinstance(f.get("source"), dict) and f.get("source").get("path") == source_rel
        and f.status != "retired"
    ]
    order = {f.id: (int((f.get("source") or {}).get("line") or 0), f.id) for f in existing}
    by_key = {str(f.get("import_key")): f for f in existing}
    matched: dict[int, tuple[Fact, str]] = {}
    for i, cand in enumerate(parsed.candidates):
        fact = by_key.get(cand.key)
        if fact is not None:
            matched[i] = (fact, "unchanged" if _same(cand, fact) else "changed")
    taken = {fact.id for fact, _ in matched.values()}
    loose = [f for f in sorted(existing, key=lambda f: order[f.id]) if f.id not in taken]

    def group(fact: Fact) -> str:
        parent = by_id.get(str(fact.get("parent")))
        return str(parent.get("import_key")) if parent is not None and parent.get("import_key") else "summary"

    pairs = []
    for i, cand in enumerate(parsed.candidates):
        if i in matched or cand.kind != "achievement":
            continue
        for fact in loose:
            if fact.kind == "achievement" and group(fact) == (cand.parent_key or "summary"):
                score = _jaccard(str(cand.fields["text"]), str(fact.get("text")))
                if score >= _MIN_OVERLAP:
                    pairs.append((-score, i, order[fact.id], fact))
    for _score, i, _line, fact in sorted(pairs, key=lambda p: p[:3]):
        if i not in matched and fact.id not in taken:
            matched[i] = (fact, "changed")
            taken.add(fact.id)
    for i, cand in enumerate(parsed.candidates):
        if i in matched:
            fact, result = matched[i]
            plan.changes.append(Change(result, _decide(result, fact), cand, fact))
        else:
            plan.changes.append(Change("added", "create", cand, None))
    for fact in loose:
        if fact.id not in taken:
            plan.changes.append(Change("removed", _decide("removed", fact), None, fact))
    return plan


# --- apply ----------------------------------------------------------------------------------

@dataclass
class ImportResult:
    status: str                      # complete | noop
    plan: ImportPlan
    backup: Path | None = None


def _source_block(plan: ImportPlan, cand: Candidate) -> dict:
    return {"kind": "resume", "path": plan.source_rel, "line": cand.line, "sha256": cand.line_sha}


def apply_import(root: Path, source_rel: str = "resume.md", *, actor: str = "user") -> ImportResult:
    root = Path(root)
    with WorkspaceLock(root):
        career = open_career(root)
        plan = plan_import(root, source_rel)
        if plan.errors:
            raise MemoryOpError("\n".join(plan.errors))
        sources = store.load_sources(root)
        entry = {
            "path": source_rel, "sha256": plan.source_sha, "imported_at": models.utc_now(),
            "framework_version": versions.installed_version(), "pending_review": plan.pending_review,
        }
        recorded = sources.get(source_rel, {})
        if not plan.actionable and recorded.get("sha256") == plan.source_sha and recorded.get("pending_review") == plan.pending_review:
            return ImportResult("noop", plan)
        now = entry["imported_at"]
        taken = set(career.by_id())
        resolved = {str(f.get("import_key")): f.id for f in career.facts if f.get("import_key")}
        writes: dict[Path, bytes] = {}
        specs: list[dict] = []
        for change in plan.changes:
            cand, fact = change.candidate, change.fact
            if change.action == "create":
                new_id = ids.new_id(store.ID_KIND[cand.kind], taken)
                taken.add(new_id)
                resolved[cand.key] = new_id
                fields = dict(cand.fields)
                if cand.kind == "achievement":
                    fields = {"parent": resolved.get(cand.parent_key) if cand.parent_key else None, **fields}
                fm = store.new_frontmatter(cand.kind, new_id, fields, _source_block(plan, cand), "imported", now)
                fm["import_key"] = cand.key
                created = store.new_fact(root, cand.kind, fm)
                writes[created.path] = store.render(created)
                specs.append(event_spec(new_id, "memory.fact_added", f"imported {cand.kind}: {store.heading(cand.kind, fm)}",
                                        actor=actor, new="claimed"))
            elif change.action in ("update", "unstale", "mark_stale"):
                fm = dict(fact.fm)
                if change.action == "update":
                    fm.update(cand.fields)
                    fm["source"], fm["import_key"] = _source_block(plan, cand), cand.key
                    fm.pop("stale", None)
                    note = "updated from the resume"
                elif change.action == "unstale":
                    fm.pop("stale", None)
                    note = "its source line is back in the resume"
                else:
                    fm["stale"] = True
                    note = "marked stale: its source line is gone from the resume"
                fm["updated_at"] = now
                updated = Fact(fm, fact.body, fact.path)
                writes[updated.path] = store.render(updated)
                specs.append(event_spec(fact.id, "memory.fact_updated", f"{fact.kind}: {note}", actor=actor,
                                        prev=fact.status, new=fact.status))
        backup = backup_helpers.new_backup_dir(root, "memory-import")
        sources[source_rel] = entry
        writes[root / store.SOURCES_REL] = store.render_sources(sources)
        items = [(p.relative_to(root).as_posix(), p.read_bytes()) for p in writes if p.exists()]
        backup_helpers.backup_files(backup, items)
        files = [
            {"path": p.relative_to(root).as_posix(), "existed": p.exists(),
             "before_sha256": store.sha256_hex(p.read_bytes()) if p.exists() else None, "after_sha256": None}
            for p in writes
        ]
        manifest = {
            "version": 1, "kind": "memory-import", "created_at": now, "tool_version": versions.installed_version(),
            "source": source_rel, "source_sha256": plan.source_sha, "status": "pending", "files": files,
        }
        backup_helpers.write_manifest(backup, manifest)
        tx = Transaction()
        try:
            for path, data in writes.items():
                tx.write(path, data)
            specs.append({
                "type": "memory.imported", "actor": actor, "source": "cli",
                "action": (f"imported {source_rel}: {plan.count('added')} added, {plan.count('changed')} changed, "
                           f"{plan.count('removed')} removed, {plan.count('unchanged')} unchanged, "
                           f"{plan.pending_review} need review"),
                "artifacts": [f"{backup.relative_to(root).as_posix()}/manifest.json", source_rel],
            })
            ledger.append_events(root, specs)
        except BaseException:
            tx.rollback()
            manifest["status"] = "rolled_back"
            backup_helpers.write_manifest(backup, manifest)
            raise
        for record in files:
            current = root / record["path"]
            record["after_sha256"] = store.sha256_hex(current.read_bytes()) if current.exists() else None
        manifest["status"] = "complete"
        backup_helpers.write_manifest(backup, manifest)
        return ImportResult("complete", plan, backup)
