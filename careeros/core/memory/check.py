"""The evidence check: do the checkable claims in a draft trace to the career memory?

Deterministic: no model judges prose and nothing leaves the machine. A pass means every *detected* claim
(numbers, years, durations, technologies, employers, schools, titles, certifications) is supported by the
memory. It never means every claim in the draft was detected; the result lists what was not evaluated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from careeros.core import models
from careeros.core.memory import facets, store
from careeros.core.memory.lexicon import Lexicon
from careeros.core.memory.normalize import metric_key, norm_org, norm_text, scan, split_sentences
from careeros.core.memory.store import Career, Fact

CODES = {
    "number": "CHK001", "year": "CHK002", "technology": "CHK003", "employer": "CHK004", "school": "CHK005",
    "title": "CHK006", "certification": "CHK007", "duration": "CHK008", "name": "CHK009",
}
FIX = {
    "CHK001": "remove the figure or reword without it; if it is true, add it with `careeros memory add`",
    "CHK002": "remove the year, or add the dated fact to your memory",
    "CHK003": "remove the technology, or add it as a skill or inside a real achievement if it is true",
    "CHK004": "remove the employer, or add the experience with `careeros memory add --kind experience`",
    "CHK005": "remove the school, or add the education with `careeros memory add --kind education`",
    "CHK006": "use a title from your experience records, or add the role if it is true",
    "CHK007": "remove the certification, or add it with `careeros memory add --kind certification`",
    "CHK008": "state a number of years your dated experience supports, or drop the figure",
    "CHK009": "remove or fix the name; if it is real and not a claim about you, pass --allow \"<name>\"",
    "CHK010": "rewrite the sentence so its claims come from one fact, or ask the user and add the fact",
    "CHK020": "confirm the fact with `careeros memory confirm <id>` (a person must do this)",
    "CHK030": "import your resume with `careeros memory import`, or add facts with `careeros memory add`",
    "CHK031": "run `careeros memory import` to review what changed",
}
NOT_EVALUATED_CATEGORIES = [
    "whether prose is true", "wording and meaning", "responsibilities, scope and team-size wording",
    "qualitative outcomes (improved, faster, reliable)", "a lowercase or sentence-initial unknown name with no cue",
    "technologies in neither the lexicon nor your memory", "numbers written as words", "languages other than English",
]
LIST_SECTIONS = {"skills", "technologies", "tech stack", "technical skills", "tools", "stack", "technical"}
_LIST_CUE = re.compile(
    r"\b(?:experience (?:with|in)|proficient (?:in|with)|skilled (?:in|with)|familiar (?:with|in)|such as|including)\s|"
    r"\b(?:stack|technologies)\s*:\s", re.I)
_LIST_PREFIX = re.compile(r"^(?:skills|technologies|tech stack|technical skills|tools|stack|tech)\s*:", re.I)
_GREETING = re.compile(r"^(?:dear|hi|hello|hey|greetings|thanks|thank you|regards|best|sincerely|cheers|yours)\b", re.I)
_NAME = r"(?-i:[A-Z][\w&'’-]*)(?:\s+(?:(?-i:[A-Z][\w&'’-]*)|of|&))*"
_EMPLOYER_CUES = [
    re.compile(rf"\b(?:worked|working|employed|interned|served|volunteered)\b(?:\s+\w+){{0,3}}?\s+(?:at|for)\s+(?P<n>{_NAME})", re.I),
    re.compile(rf"\b(?:experience|time|tenure|years|stint)\s+(?:\w+\s+){{0,2}}?at\s+(?P<n>{_NAME})", re.I),
    re.compile(rf"\b(?:joined|left|leaving)\s+(?P<n>{_NAME})", re.I),
]
_SCHOOL_CUE = re.compile(
    r"\b(?:studied|studying|graduated|graduate|degree|bachelor(?:'s)?|master(?:'s)?|b\.?tech|m\.?tech|b\.?sc|m\.?sc|phd|mba|diploma)\b"
    rf"[^.;\n]*?\b(?:at|from)\s+(?P<n>{_NAME})", re.I)
_CERT_CUE = re.compile(rf"\b(?:certified|certification|certificate)\b(?:\s+(?:in|as|for|by|from))?\s+(?P<n>{_NAME})", re.I)
_TITLE_AT = re.compile(rf"(?P<t>{_NAME})\s+(?:at|@)\s+(?P<n>{_NAME})")
_PIPE = re.compile(r"^\s*(?:#{1,6}\s*)?(?P<e>[^|]+?)\s*\|\s*(?P<t>[^|]+?)\s*$")
_FILLER = re.compile(
    r"^(?:\s+(?:of|professional|hands-on|industry|commercial|practical|experience|in|with|using|working|work|"
    r"building|writing|developing))*\s+$|^\s*$", re.I)
_APPLYING = re.compile(r"\b(?:join|apply|applying|interested in|excited|seeking|would love|hope to|keen)\b", re.I)
_TOKEN = re.compile(r"(?-i:[A-Z][\w&'’-]*)")
_WORDS = re.compile(r"[\w'’&-]+")


@dataclass
class Atom:
    kind: str
    raw: str
    key: object
    start: int
    end: int
    status: str = "supported"        # supported | unsupported | review | allowed
    code: str = ""
    reason: str = ""
    holders: tuple[str, ...] = ()
    holder_status: tuple[str, ...] = ()


@dataclass
class Finding:
    code: str
    sentence: str
    atom: str
    reason: str
    fix: str

    def to_dict(self) -> dict:
        return {"code": self.code, "sentence": self.sentence, "atom": self.atom, "reason": self.reason, "fix": self.fix}


@dataclass
class CheckResult:
    supported: list[dict] = field(default_factory=list)
    review_required: list[Finding] = field(default_factory=list)
    not_evaluated: list[str] = field(default_factory=list)
    info: list[Finding] = field(default_factory=list)
    allowed_mentions: list[str] = field(default_factory=list)
    claimed_support: int = 0
    memory_stale: bool = False

    @property
    def ok(self) -> bool:
        return not self.review_required

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "supported": self.supported,
            "review_required": [f.to_dict() for f in self.review_required],
            "not_evaluated": {"sentences": self.not_evaluated, "categories": NOT_EVALUATED_CATEGORIES},
            "info": [f.to_dict() for f in self.info],
            "allowed_mentions": self.allowed_mentions,
            "supported_by_claimed": self.claimed_support,
            "memory_stale": self.memory_stale,
        }


@dataclass
class Record:
    """One place where claims co-occur: an achievement, project, experience, education, certification or skill."""

    id: str
    kind: str
    status: str
    techs: frozenset = frozenset()
    metrics: tuple = ()
    employer: str | None = None
    titles: frozenset = frozenset()
    school: str | None = None
    certs: frozenset = frozenset()
    span: tuple[int, int] | None = None
    points: frozenset = frozenset()


def _year_month(value: str, now_year: int, now_month: int) -> int:
    if value == "present":
        return now_year * 12 + now_month
    year, month = value.split("-")
    return int(year) * 12 + int(month)


def _title_variants(title: str) -> set[str]:
    head = re.split(r"[,(|–—-]\s", title, maxsplit=1)[0]
    return {v for v in (norm_text(title), norm_text(head)) if v}


def _strip_suffix_words(name: str, suffixes: frozenset[str]) -> str:
    words = name.split()
    while len(words) > 1 and words[-1].lower().strip(".,") in suffixes:
        words.pop()
    return " ".join(words)


def _word_re(text: str) -> re.Pattern:
    return re.compile(rf"(?<![\w]){re.escape(text)}(?![\w])", re.I)


def _overlaps(start: int, end: int, used: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in used)


class Memory:
    """The active facts, indexed for lookup."""

    def __init__(self, career: Career, lexicon: Lexicon, now: str) -> None:
        self.lx = lexicon
        self.now_year, self.now_month = int(now[:4]), int(now[5:7])
        self.records: list[Record] = []
        self.employers: dict[str, str] = {}   # display form -> normalised key
        self.schools: dict[str, str] = {}
        self.titles: dict[str, str] = {}
        self.cert_names: list[str] = []
        self.known_words: set[str] = set()
        by_id = career.by_id()
        active = career.active()
        intervals: list[tuple[int, int]] = []
        by_employer: dict[str, list[tuple[int, int]]] = {}
        for fact in active:
            self._collect_words(fact)
            record = self._record(fact, by_id)
            if record is None:
                continue
            self.records.append(record)
            if fact.kind == "experience":
                interval = (_year_month(str(fact.get("start")), self.now_year, self.now_month),
                            _year_month(str(fact.get("end")), self.now_year, self.now_month))
                intervals.append(interval)
                by_employer.setdefault(record.employer or "", []).append(interval)
        self.months = self._union(intervals)
        self.employer_months = {k: self._union(v) for k, v in by_employer.items()}
        self.has_content = any(
            f.kind in ("experience", "achievement", "project", "skill", "education", "certification") for f in active)

    @staticmethod
    def _union(intervals: list[tuple[int, int]]) -> int:
        total, last_end = 0, None
        for start, end in sorted(intervals):
            if last_end is None or start > last_end:
                total += max(0, end - start)
                last_end = end
            elif end > last_end:
                total += end - last_end
                last_end = end
        return total

    def _collect_words(self, fact: Fact) -> None:
        for key in ("employer", "school", "degree", "field", "location", "name", "category", "issuer", "title"):
            value = fact.get(key)
            if isinstance(value, str):
                self.known_words.update(w.lower() for w in _WORDS.findall(value))
        for headline in fact.get("headlines") or ():
            self.known_words.update(w.lower() for w in _WORDS.findall(str(headline)))

    def _register_name(self, table: dict[str, str], name: str) -> None:
        key = norm_org(name, self.lx.org_suffixes)
        table[name] = key
        table[_strip_suffix_words(name, self.lx.org_suffixes)] = key

    def _register_title(self, title: str) -> None:
        head = re.split(r"[,(|–—-]\s", title, maxsplit=1)[0].strip()
        for display in (title, head):
            if display:
                self.titles[display] = norm_text(display)

    def _record(self, fact: Fact, by_id: dict[str, Fact]) -> Record | None:
        lx, kind = self.lx, fact.kind
        techs = {lx.canonical_tech(t) or t for t in (fact.get("technologies") or ()) if isinstance(t, str)}
        metrics = tuple((*metric_key(m["value"], m["unit"]), bool(m.get("plus"))) for m in (fact.get("metrics") or ()))
        rec = Record(fact.id, kind, fact.status, frozenset(techs), metrics)
        if kind == "skill":
            name = str(fact.get("name"))
            rec.techs = frozenset({lx.canonical_tech(name) or name})
        elif kind in ("experience", "achievement"):
            source = fact if kind == "experience" else by_id.get(str(fact.get("parent")))
            if source is None or source.kind != "experience":
                return rec if kind == "achievement" else None
            if source.status not in store.ACTIVE:
                return rec if kind == "achievement" else None
            employer, title = str(source.get("employer")), str(source.get("title"))
            end = str(source.get("end"))
            rec.employer = norm_org(employer, lx.org_suffixes)
            rec.titles = frozenset(_title_variants(title))
            rec.span = (int(str(source.get("start"))[:4]), self.now_year if end == "present" else int(end[:4]))
            if kind == "experience":
                self._register_name(self.employers, employer)
                self._register_title(title)
        elif kind == "project":
            pass
        elif kind == "education":
            school = str(fact.get("school"))
            rec.school = norm_org(school, lx.org_suffixes)
            self._register_name(self.schools, school)
            if fact.get("end"):
                rec.points = frozenset({int(str(fact.get("end"))[:4])})
        elif kind == "certification":
            name = str(fact.get("name"))
            rec.certs = frozenset({(lx.certs.get(name.lower()) or name).lower()})
            self.cert_names.append(name)
            if fact.get("year"):
                rec.points = frozenset({int(str(fact.get("year"))[:4])})
        elif kind == "identity":
            rec.titles = frozenset(v for h in (fact.get("headlines") or ()) for v in _title_variants(str(h)))
            for headline in fact.get("headlines") or ():
                self._register_title(str(headline))
        else:
            return None
        return rec

    def holds(self, record: Record, atom: Atom) -> bool:
        kind, key = atom.kind, atom.key
        if kind == "technology":
            return key in record.techs
        if kind == "number":
            value, unit, plus = key
            return any(m[0] == value and m[1] == unit and (m[2] or not plus) for m in record.metrics)
        if kind == "year":
            return bool(record.span and record.span[0] <= key <= record.span[1]) or key in record.points
        if kind == "employer":
            return record.employer == key
        if kind == "school":
            return record.school == key
        if kind == "title":
            return key in record.titles
        if kind == "certification":
            return key in record.certs
        return False

    def holders(self, atom: Atom) -> list[Record]:
        return [r for r in self.records if self.holds(r, atom)]


class Checker:
    def __init__(self, memory: Memory, allow: list[str], against: tuple[str, str] | None) -> None:
        lx = self.lx = memory.lx
        self.m = memory
        self.allow_norm = {norm_text(t) for t in allow}
        self.allow_words = {w.lower() for t in allow for w in _WORDS.findall(t)}
        self.allow_numbers = {metric_key(n.value, n.unit) for t in allow for n in scan(t).numbers}
        self.allow_techs = {c for t in allow if (c := lx.canonical_tech(t))}
        self.against_names = [n for n in (against or ()) if n]
        self.against_title_keys = {norm_text(against[1])} if against and against[1] else set()
        self.against_company_key = norm_org(against[0], lx.org_suffixes) if against and against[0] else None
        seniority = "|".join(sorted(map(re.escape, lx.seniority), key=len, reverse=True))
        roles = "|".join(sorted(map(re.escape, lx.role_nouns), key=len, reverse=True))
        self._sen_run = re.compile(
            rf"\b(?:(?:{seniority})\.?\s+)+(?:[A-Za-z][\w&+/-]*\s+){{0,3}}?(?:{roles})\b"
            rf"(?:\s+(?:of|for|in)\s+[A-Za-z][\w&-]*(?:\s+[A-Za-z][\w&-]*){{0,2}})?", re.I)
        self._head_of = re.compile(
            r"\b(?:head|chief|director|manager|vice president)\s+(?:of|for)\s+(?-i:[A-Z])[\w&-]*"
            r"(?:\s+(?-i:[A-Z])[\w&-]*){0,2}", re.I)
        self._acronym = re.compile(r"\b(?:VP|CTO|CEO|CIO|COO|CFO)\b(?:\s+(?:of|for)\s+[A-Za-z][\w&-]*(?:\s+[A-Za-z][\w&-]*){0,2})?")
        self._cue_role = re.compile(
            r"(?:\bas\s+(?:an?\s+|the\s+)?|\bI(?:'m| am| was)\s+(?:an?\s+|the\s+)?|\bwas\s+(?:an?\s+|the\s+)?)"
            rf"(?P<t>(?:[\w&+/.-]+\s+){{0,3}}(?:{roles}))\b", re.I)
        self._known_titles = sorted(memory.titles, key=len, reverse=True)

    # ------------------------------------------------------------ detection
    def detect(self, text: str) -> list[Atom]:
        atoms: list[Atom] = []
        used: list[tuple[int, int]] = []
        analysis = facets.analyze(text, self.lx)

        def add(atom: Atom) -> None:
            atoms.append(atom)
            used.append((atom.start, atom.end))

        def free(start: int, end: int) -> bool:
            return not _overlaps(start, end, used)

        for d in analysis.scan.durations:
            tied = any(
                t.start >= d.end and t.start - d.end <= 40 and _FILLER.match(text[d.end:t.start])
                for t in analysis.techs)
            add(Atom("duration", text[d.start:d.end], (d.years, d.plus, tied), d.start, d.end))
        self._certifications(text, add, free)
        self._titles(text, add, free)
        for t in analysis.techs:
            if free(t.start, t.end):
                add(Atom("technology", text[t.start:t.end], t.canonical, t.start, t.end))
        used.extend(analysis.version_spans)
        for n in analysis.scan.numbers:
            if free(n.start, n.end):
                add(Atom("number", n.raw, (*metric_key(n.value, n.unit), n.plus), n.start, n.end))
        for y in analysis.scan.years:
            if free(y.start, y.end):
                add(Atom("year", text[y.start:y.end], y.year, y.start, y.end))
        self._known_names(text, add, free)
        self._cues(text, add, free)
        for name in self.against_names:
            for match in _word_re(name).finditer(text):
                if free(match.start(), match.end()):
                    used.append(match.span())
        if not _GREETING.match(text):
            self._residual(text, add, free)
        atoms.sort(key=lambda a: a.start)
        return atoms

    def _certifications(self, text: str, add, free) -> None:
        for alias, canonical in sorted(self.lx.cert_aliases, key=lambda p: len(p[0]), reverse=True):
            pattern = _word_re(alias) if len(alias) > 6 else re.compile(rf"(?<![\w]){re.escape(alias)}(?![\w])")
            for match in pattern.finditer(text):
                if free(match.start(), match.end()):
                    add(Atom("certification", match.group(0), canonical.lower(), match.start(), match.end()))
        for name in sorted(self.m.cert_names, key=len, reverse=True):
            key = (self.lx.certs.get(name.lower()) or name).lower()
            for match in _word_re(name).finditer(text):
                if free(match.start(), match.end()):
                    add(Atom("certification", match.group(0), key, match.start(), match.end()))

    def _has_role(self, text: str) -> bool:
        words = {w.lower() for w in re.findall(r"[A-Za-z]+", text)}
        return bool(words & (self.lx.role_nouns | self.lx.title_words))

    def _titles(self, text: str, add, free) -> None:
        pipe = _PIPE.match(text)
        if pipe and (self._has_role(pipe.group("t")) or norm_text(pipe.group("t")) in set(self.m.titles.values())):
            if free(pipe.start("e"), pipe.end("e")):
                self._employer_atom(pipe.group("e").strip(), pipe.start("e"), pipe.end("e"), add)
            if free(pipe.start("t"), pipe.end("t")):
                self._title_atom(pipe.group("t"), pipe.start("t"), pipe.end("t"), add)
            return
        claims = [m.span("t") for m in self._cue_role.finditer(text) if not _APPLYING.search(text[:m.start()])]

        def claimed(start: int, end: int) -> bool:
            return any(start < e and s < end for s, e in claims)

        for pattern in (self._sen_run, self._head_of, self._acronym):
            for match in pattern.finditer(text):
                if free(match.start(), match.end()):
                    self._title_atom(match.group(0), match.start(), match.end(), add, claim=claimed(match.start(), match.end()))
        for display in self._known_titles:
            for match in _word_re(display).finditer(text):
                if free(match.start(), match.end()):
                    self._title_atom(match.group(0), match.start(), match.end(), add, claim=claimed(match.start(), match.end()))
        for match in self._cue_role.finditer(text):
            if free(match.start("t"), match.end("t")):
                self._title_atom(match.group("t"), match.start("t"), match.end("t"), add, claim=True)

    def _title_atom(self, raw: str, start: int, end: int, add, claim: bool = False) -> None:
        raw = raw.strip().rstrip(".,;:")
        key = norm_text(raw)
        kind = "context" if key in self.against_title_keys and not claim else "title"
        add(Atom(kind, raw, key, start, start + len(raw)))

    def _employer_atom(self, raw: str, start: int, end: int, add) -> None:
        key = norm_org(raw, self.lx.org_suffixes)
        kind = "context" if self.against_company_key and key == self.against_company_key else "employer"
        add(Atom(kind, raw, key, start, end))

    def _known_names(self, text: str, add, free) -> None:
        for kind, table in (("employer", self.m.employers), ("school", self.m.schools)):
            for display in sorted(table, key=len, reverse=True):
                for match in _word_re(display).finditer(text):
                    if free(match.start(), match.end()) and not self._continues_name(text, match.end()):
                        add(Atom(kind, match.group(0), table[display], match.start(), match.end()))

    def _continues_name(self, text: str, end: int) -> bool:
        """'Acme Systems' is not 'Acme': a known name followed by another capitalised word is a different name."""
        follower = re.match(r"\s+([A-Z][\w&'’-]*)", text[end:])
        if not follower:
            return False
        word = follower.group(1).lower()
        return word not in self.lx.stoplist and word not in self.lx.org_suffixes

    @staticmethod
    def _trim(name: str) -> str:
        words = name.split()
        while words and words[-1].lower() in ("of", "&"):
            words.pop()
        return " ".join(words)

    def _cues(self, text: str, add, free) -> None:
        for pattern in _EMPLOYER_CUES:
            for match in pattern.finditer(text):
                name, start = self._trim(match.group("n")), match.start("n")
                if name and free(start, start + len(name)):
                    add(Atom("employer", name, norm_org(name, self.lx.org_suffixes), start, start + len(name)))
        for match in _SCHOOL_CUE.finditer(text):
            name, start = self._trim(match.group("n")), match.start("n")
            if name and free(start, start + len(name)):
                add(Atom("school", name, norm_org(name, self.lx.org_suffixes), start, start + len(name)))
        for match in _CERT_CUE.finditer(text):
            name, start = self._trim(match.group("n")), match.start("n")
            if name and free(start, start + len(name)):
                add(Atom("certification", name, name.lower(), start, start + len(name)))
        for match in _TITLE_AT.finditer(text):
            title, company, start = self._trim(match.group("t")), self._trim(match.group("n")), match.start("n")
            if self._has_role(title) and free(start, start + len(company)):
                self._employer_atom(company, start, start + len(company), add)

    def _residual(self, text: str, add, free) -> None:
        """Capitalised spans that nothing else explained: possible employers, schools or people."""
        runs: list[list[re.Match]] = []
        for token in _TOKEN.finditer(text):
            if not free(token.start(), token.end()):
                runs.append([])
                continue
            word = token.group(0).lower()
            if word in self.lx.stoplist or word in self.m.known_words:
                runs.append([])
            elif runs and runs[-1] and text[runs[-1][-1].end():token.start()] == " ":
                runs[-1].append(token)
            else:
                runs.append([token])
        for run in runs:
            if run and text[:run[0].start()].strip(" #*-•>\t") == "":
                run = run[1:]  # sentence-initial capital: not evidence of a name
            if run:
                raw = text[run[0].start():run[-1].end()]
                add(Atom("name", raw, norm_text(raw), run[0].start(), run[-1].end()))

    # ------------------------------------------------------------ judgement
    def _allowed(self, atom: Atom) -> bool:
        if atom.kind == "technology":
            return atom.key in self.allow_techs or norm_text(atom.raw) in self.allow_norm
        if atom.kind == "number":
            return (atom.key[0], atom.key[1]) in self.allow_numbers
        if atom.kind == "name":
            return atom.key in self.allow_norm or {w.lower() for w in _WORDS.findall(atom.raw)} <= self.allow_words
        return atom.key in self.allow_norm or norm_text(atom.raw) in self.allow_norm

    def _support(self, atom: Atom) -> None:
        if atom.kind == "name":
            if self._allowed(atom):
                atom.status = "allowed"
            else:
                atom.status, atom.code = "review", "CHK009"
                atom.reason = f"{atom.raw!r} looks like a name, but it is not in your memory"
            return
        if atom.kind == "duration":
            years, _plus, tied = atom.key
            derived = self.m.months // 12
            if tied:
                atom.status, atom.code = "review", "CHK008"
                atom.reason = f"{atom.raw}: years with a specific technology cannot be derived from your dated experience"
            elif self.m.months == 0 or years > derived:
                atom.status, atom.code = "unsupported", "CHK008"
                atom.reason = f"{atom.raw}: your dated experience covers {derived} year(s)"
            return
        holders = self.m.holders(atom)
        if holders:
            atom.holders = tuple(r.id for r in holders)
            atom.holder_status = tuple(r.status for r in holders)
        elif self._allowed(atom):
            atom.status = "allowed"
        else:
            atom.status, atom.code = "unsupported", CODES[atom.kind]
            atom.reason = f"{atom.raw!r} is not supported by any fact in your memory"

    def _list_exempt(self, sentence: str, atoms: list[Atom], list_line: bool) -> bool:
        if any(a.kind != "technology" for a in atoms):
            return False
        if list_line:
            return True
        cue = _LIST_CUE.search(sentence)
        return bool(cue) and all(a.start >= cue.end() for a in atoms)

    def judge(self, sentence: str, list_line: bool, result: CheckResult, require_confirmed: bool) -> None:
        atoms = [a for a in self.detect(sentence) if a.kind != "context"]
        if not atoms:
            result.not_evaluated.append(sentence)
            return
        for atom in atoms:
            self._support(atom)
        employers = [a for a in atoms if a.kind == "employer" and a.status == "supported"]
        for atom in atoms:
            if atom.kind == "duration" and atom.status == "supported":
                if not employers:
                    continue
                emp = min(employers, key=lambda e: abs(e.start - atom.start))
                covered = self.m.employer_months.get(emp.key, 0) // 12
                if atom.key[0] > covered:
                    atom.status, atom.code = "unsupported", "CHK008"
                    atom.reason = f"{atom.raw}: your dated experience at that employer covers {covered} year(s)"
        findings = [Finding(a.code, sentence, a.raw, a.reason, FIX[a.code]) for a in atoms if a.status in ("unsupported", "review")]
        if not findings:
            findings = self._relationship(sentence, atoms, list_line)
        result.review_required.extend(findings)
        if findings:
            return
        for atom in atoms:
            if atom.status == "allowed":
                result.allowed_mentions.append(atom.raw)
            elif atom.status == "supported":
                claimed_only = bool(atom.holder_status) and all(s == "claimed" for s in atom.holder_status)
                result.supported.append({
                    "sentence": sentence, "kind": atom.kind, "claim": atom.raw,
                    "facts": list(atom.holders), "claimed_only": claimed_only,
                })
                if claimed_only:
                    result.claimed_support += 1
                    if require_confirmed:
                        result.review_required.append(
                            Finding("CHK020", sentence, atom.raw, "supported only by claimed facts", FIX["CHK020"]))

    def _relationship(self, sentence: str, atoms: list[Atom], list_line: bool) -> list[Finding]:
        """Claims in one sentence must come from one fact; an allowed term can never supply that fact."""
        related = [a for a in atoms if a.kind != "duration" and a.status in ("supported", "allowed")]
        if len(related) < 2 or self._list_exempt(sentence, related, list_line):
            return []
        supported = [a for a in related if a.status == "supported"]
        allowed = [a for a in related if a.status == "allowed"]
        if allowed and supported:
            names = ", ".join(a.raw for a in allowed)
            return [Finding("CHK010", sentence, names,
                            f"{names} was allowed, but no fact in your memory ties it to the other claims in this sentence",
                            FIX["CHK010"])]
        if not supported:
            return []
        holders = [r for r in self.m.records if all(self.m.holds(r, a) for a in supported)]
        if not holders:
            detail = "; ".join(f"{a.raw} ({', '.join(a.holders) or 'no fact'})" for a in supported)
            return [Finding("CHK010", sentence, ", ".join(a.raw for a in supported),
                            f"each claim is supported on its own, but no single fact holds them together: {detail}",
                            FIX["CHK010"])]
        for atom in supported:
            atom.holders = tuple(r.id for r in holders)
            atom.holder_status = tuple(r.status for r in holders)
        return []


def _prepare_lines(draft: str) -> list[tuple[str, bool]]:
    """Each content unit of the draft with markdown stripped, and whether it sits in a skills-style section.

    A plain line is joined onto the unit above only when that unit is unterminated and the line begins lowercase or with a
    digit (a wrapped continuation). A capitalised continuation starts a new unit: a known limit.
    """
    draft = re.sub(r"<!--.*?-->", "", draft, flags=re.S)
    out: list[tuple[str, bool]] = []
    section, in_fence = "", False
    pending: list[str] = []
    pending_list = False
    pending_locked = False

    def flush() -> None:
        nonlocal pending
        if pending:
            out.append((" ".join(pending), pending_list))
            pending = []

    def clean(text: str) -> str:
        return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).replace("**", "").replace("`", "").strip()

    for raw in draft.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence or not line.strip() or re.fullmatch(r"\s*([-=_*]\s*){3,}", line):
            flush()
            continue
        heading = re.match(r"^\s*#{1,6}\s+(.*)$", line)
        bold_only = re.match(r"^\s*\*\*([^*]+?)\*\*:?\s*$", line)
        if bold_only or (heading and not _PIPE.match(line)):
            flush()
            section = norm_text((heading or bold_only).group(1))
            continue
        if _PIPE.match(line):
            flush()
            text = clean(line.strip())
            if text:
                out.append((text, section in LIST_SECTIONS))
            continue
        stripped = re.sub(r"^\s*(?:#{1,6}\s+|[-*+•]\s+|\d+[.)]\s+)", "", line)
        text = clean(stripped)
        if not text:
            flush()
            continue
        is_item = stripped != line
        locked = bool(_GREETING.match(text) or _LIST_PREFIX.match(text) or line.lstrip().startswith("**"))
        joinable = (
            bool(pending) and not is_item and not locked and not pending_locked
            and not pending[-1].rstrip().endswith((".", "!", "?", ":", ";"))
            and (text[0].islower() or text[0].isdigit())
        )
        if not joinable:
            flush()
            pending_locked = locked
        pending.append(text)
        pending_list = section in LIST_SECTIONS
    flush()
    return out


def run_check(
    root: Path,
    draft: str,
    *,
    allow: list[str] | None = None,
    against: tuple[str, str] | None = None,
    require_confirmed: bool = False,
    career: Career | None = None,
) -> CheckResult:
    """Check a draft against the career memory. The caller must make sure the memory has no errors."""
    root = Path(root)
    career = career or store.load_career(root)
    memory = Memory(career, store.lexicon_for(root, career), models.utc_now())
    result = CheckResult()
    result.memory_stale = any(i.code == "MEM006" for i in career.issues)
    if result.memory_stale:
        result.info.append(Finding("CHK031", "", "", "a source file changed since it was imported", FIX["CHK031"]))
    if not memory.has_content:
        result.review_required.append(Finding("CHK030", "", "", "the career memory has no usable facts", FIX["CHK030"]))
        return result
    checker = Checker(memory, allow or [], against)
    for line, in_list in _prepare_lines(draft):
        list_line = in_list or bool(_LIST_PREFIX.match(line))
        for sentence in split_sentences(line):
            checker.judge(sentence, list_line, result, require_confirmed)
    return result
