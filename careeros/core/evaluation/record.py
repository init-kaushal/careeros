"""The evaluation record `jobs/discovered/<slug>/evaluation.md`: parsing, agent-half validation, freshness stamps, the
result and its rendered body (spec sections 4, 8, 9 and 16). Read-only: nothing here writes a file; `skeleton` and
`to_bytes` return bytes for the caller (`careeros evaluate`, Task E) to write transactionally.

Record layout: frontmatter `id` (`evl_...`), `type: evaluation`, `schema: 1`, `job`, `created_at`, `agent:` (written by
the agent; `{}` in a skeleton) and, once evaluated, `result:` (written by code), followed by a body that is exactly
`render_body(result)`. A skeleton has no `result:`; that is not an error.

Agent half (`parse_agent`, `validate_agent`): codes per spec 8 and 16.1. YAML 1.1 reads unquoted `yes`/`no` (and
`on`/`off`) as booleans; `judgement` and `needed` are turned back into the words before any enum is built and before the
`inputs` stamp is hashed, so quoting never changes anything. Every other wrong type is EVL003, so the scorer's defensive
coercions never see invalid input. Quotes are verified as substrings of `normalize_text(jd.md)` after the same
normalisation (the one the `jd` stamp uses): reflowed whitespace passes, a different case does not.

Stamps (spec 9; `result.stamps`, five strings):

* `jd`: SHA-256 of `normalize_text(jd.md)`.
* `memory`: SHA-256 of the canonical JSON of `memory_canonical`: every fact (retired and disputed included, sorted by
  id) with its frontmatter minus the bookkeeping keys `schema`, `origin`, `source`, `created_at`, `updated_at`,
  `confirmed_at` and `stale`; every number in a fact is a decimal string (`12.5`, `60`), so no float is hashed and
  `60` and `60.0` agree. **Decided here (the spec lists facts only):** the effective lexicon and the two clock-dependent
  quantities the derivation reads (whole years of active dated experience and the ids of the experiences that
  contribute to them) are part of the same stamp, so a lexicon edit, a CareerOS lexicon upgrade or the calendar crossing
  a year boundary makes the record stale instead of looking like a tampered basis.
* `rubric`: SHA-256 of the parsed fit rows (normalised label and weight, sorted) and the rubric state, **plus (decided
  here) the parsed `## Markets` rows** (normalised label and the exact floor and sponsorship cells) and the table's
  ambiguous/unusable flags: the cap inputs are verified against that table, so editing it must read as stale.
* `rules`: the rules version string.
* `inputs`: SHA-256 of the agent half without `updated_at` (dates as ISO strings, floats as decimal strings).

`result.basis` (spec 16.2) is separate from the stamps: the exact scorer input built by `derive.Basis.complete`, with its
hash in `result.basis_sha256`. Scores are stored as `score.stored_score` floats (four decimals).
"""

from __future__ import annotations

import dataclasses
import math
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from careeros.core import ids, models
from careeros.core.evaluation import derive, markets as markets_mod, rubric as rubric_mod, rules, score
from careeros.core.evaluation.canon import canonical_json, fixed4, normalize_text, sha256_hex
from careeros.core.evaluation.markets import MarketError, Markets
from careeros.core.evaluation.model import (
    FitJudgement, JobFacts, Judgement, Kind, Link, Listing, MarketBasis, ReqClass, Requirement, SourceConfidence,
    Stamps, Strength,
)
from careeros.core.evaluation.rubric import Rubric, norm_label
from careeros.core.memory import store
from careeros.core.memory.check import Memory
from careeros.core.memory.lexicon import Lexicon, load_lexicon
from careeros.core.memory.store import Career
from careeros.core.models import Issue
from careeros.core.workspace import WorkspaceError, join_frontmatter, normalize_value, split_frontmatter

EVALUATION_FILE = "evaluation.md"
JD_FILE = "jd.md"
SCHEMA = 1
STAMP_KEYS = ("jd", "memory", "rubric", "rules", "inputs")
RESULT_KEYS = ("tier", "combined", "coverage", "fit", "confidence", "caps", "action_items", "requirements",
               "stamps", "basis_sha256", "basis")
SKELETON_BODY = ("\n# Evaluation\n\nNot evaluated yet. Fill in `agent:` (see the `evaluate` skill), then run "
                 "`careeros evaluate <job>`. Everything below `agent:` is written by code.\n")

_NOT_CONTENT = frozenset({"schema", "origin", "source", "created_at", "updated_at", "confirmed_at", "stale"})
_AGENT_KEYS = ("updated_at", "requirements", "fit", "job_facts")
_REQ_KEYS = ("id", "class", "kind", "term", "years", "text", "quote", "links")
_LINK_KEYS = ("fact", "strength", "why")
_FIT_KEYS = ("row", "judgement", "quote")
_FACTS_KEYS = ("market", "sponsorship", "compensation")
_SPONSOR_KEYS = ("needed", "listing", "quote")
_COMP_KEYS = ("range", "floor", "source", "confidence", "as_of")
_RANGE_KEYS = ("low", "high", "currency", "basis")
_FLOOR_KEYS = ("amount", "currency", "basis")
_FIX = "fix the `agent:` half of evaluation.md (see the `evaluate` skill), then run `careeros evaluate <job>`"


class RecordError(WorkspaceError):
    """The record cannot be used: unreadable, invalid YAML, or a missing/malformed namespace or header (EVL001)."""

    def __init__(self, message: str, code: str = "EVL001") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Record:
    """A loaded evaluation file. `fm` is the frontmatter exactly as YAML read it; `rel` is the workspace-relative path."""

    path: Path
    rel: str
    fm: dict
    body: str

    @property
    def id(self) -> str:
        return str(self.fm.get("id"))

    @property
    def job(self) -> object:
        return self.fm.get("job")

    @property
    def agent(self) -> dict:
        return self.fm.get("agent") or {}

    @property
    def result(self) -> dict | None:
        return self.fm.get("result")


def _rel(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return path.resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def load(path: Path, root: Path) -> Record:
    """Read and parse an evaluation file; `root` is required so `rec.rel` is always workspace-relative (Task E must
    write exactly `rec.rel` as the path artifact of the completed event). `RecordError` (EVL001) when it cannot be used; never any other exception."""
    path = Path(path)
    rel = _rel(path, root)
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RecordError(f"{rel} cannot be read: {exc}") from None
    try:
        fm, body = split_frontmatter(text)
    except WorkspaceError as exc:
        raise RecordError(f"{rel}: {exc}") from None
    except (RecursionError, MemoryError):
        raise RecordError(f"{rel}: the frontmatter is nested too deeply to read") from None
    if fm is None:
        raise RecordError(f"{rel} has no --- frontmatter block")
    problems = []
    if fm.get("type") != "evaluation":
        problems.append(f"type is {fm.get('type')!r}, not 'evaluation'")
    if type(fm.get("schema")) is not int or fm.get("schema") != SCHEMA:
        problems.append(f"schema is {fm.get('schema')!r}, not {SCHEMA}")
    if not ids.is_valid_id("evaluation", fm.get("id")):
        problems.append(f"id {fm.get('id')!r} is not an evl_ id")
    if "agent" not in fm:
        problems.append("the agent: namespace is missing")
    elif fm["agent"] is not None and not isinstance(fm["agent"], dict):
        problems.append("agent: is not a mapping")
    if "result" in fm and not isinstance(fm["result"], dict):
        problems.append("result: is not a mapping")
    if problems:
        raise RecordError(f"{rel}: " + "; ".join(problems))
    return Record(path, rel, fm, body)


def taken_ids(root: Path, events: list) -> set[str]:
    """Every `evl_` id in use: in evaluation files (read leniently) and as a ledger entity. `--init` must avoid them."""
    taken = {e["entity"] for e in events if isinstance(e, dict) and isinstance(e.get("entity"), str)
             and e["entity"].startswith("evl_")}
    for path in sorted((Path(root) / "jobs" / "discovered").glob(f"*/{EVALUATION_FILE}")):
        try:
            fm, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, WorkspaceError, RecursionError, MemoryError):
            continue
        if fm and isinstance(fm.get("id"), str):
            taken.add(fm["id"])
    return taken


def skeleton(job_id: str, now: str, taken=()) -> bytes:
    """The `--init` file: a fresh `evl_` id (not in `taken`), the job, `created_at` and an empty `agent:`."""
    fm = {"id": ids.new_id("evaluation", taken), "type": "evaluation", "schema": SCHEMA, "job": job_id,
          "created_at": now, "agent": {}}
    return join_frontmatter(fm, SKELETON_BODY).encode("utf-8")


# --- canonical helpers ---------------------------------------------------------------------------

def _decimal(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return repr(value)
    text = format(Decimal(repr(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _plain(value: object, numbers: bool = False) -> object:
    """A hashable copy: dates as ISO strings, floats (and with `numbers` every number) as decimal strings."""
    value = normalize_value(value)
    if isinstance(value, dict):
        return {str(k): _plain(v, numbers) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, numbers) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(str(_plain(v, numbers)) for v in value)
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, float) or (numbers and isinstance(value, int)):
        return _decimal(value)
    if isinstance(value, int):
        return value
    return str(value)


def _word(value: object) -> object:
    """YAML 1.1 booleans back to the words the agent wrote (`yes`/`no`)."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return value


def agent_canonical(agent: object) -> dict:
    """The agent half as hashed for the `inputs` stamp: without `updated_at`, booleans for yes/no read as words."""
    plain = _plain(agent if isinstance(agent, dict) else {})
    plain.pop("updated_at", None)
    rows = plain.get("fit")
    for row in rows if isinstance(rows, list) else ():
        if isinstance(row, dict) and "judgement" in row:
            row["judgement"] = _word(row["judgement"])
    facts = plain.get("job_facts")
    sponsorship = facts.get("sponsorship") if isinstance(facts, dict) else None
    if isinstance(sponsorship, dict) and "needed" in sponsorship:
        sponsorship["needed"] = _word(sponsorship["needed"])
    return plain


def memory_canonical(career: Career, lexicon: Lexicon, now: str) -> dict:
    facts = [{k: _plain(v, numbers=True) for k, v in ((str(k), v) for k, v in f.fm.items()) if k not in _NOT_CONTENT}
             for f in career.facts]
    facts.sort(key=lambda f: (str(f.get("id")), canonical_json(f)))
    try:
        memory = Memory(career, lexicon, now)
        derived = {"years": memory.months // 12, "experiences": sorted(derive._contributing(career, memory))}
    except Exception:          # a memory too broken to read: it has MEM errors, so nothing is derived from it anyway
        derived = None
    return {"facts": facts, "lexicon": _plain(getattr(lexicon, "_data", None)), "derived": derived}


def jd_stamp(text: str) -> str:
    return sha256_hex(normalize_text(text).encode("utf-8"))


def memory_stamp(career: Career, lexicon: Lexicon, now: str) -> str:
    return sha256_hex(canonical_json(memory_canonical(career, lexicon, now)))


def rubric_canonical(rubric: Rubric, markets: Markets) -> dict:
    return {
        "fit": {"state": rubric.state, "rows": sorted(({"row": r.key, "weight": r.weight} for r in rubric.rows),
                                                      key=lambda r: r["row"])},
        "markets": {"rows": [{"market": key, "floor_cell": row.floor_cell, "sponsorship_cell": row.sponsorship_cell}
                             for key, row in sorted(markets.rows.items())],
                    "ambiguous": sorted(markets.ambiguous), "unusable": markets.unusable},
    }


def rubric_stamp(rubric: Rubric, markets: Markets) -> str:
    return sha256_hex(canonical_json(rubric_canonical(rubric, markets)))


def inputs_stamp(agent: object) -> str:
    return sha256_hex(canonical_json(agent_canonical(agent)))


def stamps(jd_text: str, career: Career, rubric: Rubric, rules_version: str, agent: object, *,
           lexicon: Lexicon, markets: Markets, now: str) -> Stamps:
    """The five freshness stamps of the current inputs (see the module docstring)."""
    return Stamps(jd_stamp(jd_text), memory_stamp(career, lexicon, now), rubric_stamp(rubric, markets),
                  rules_version, inputs_stamp(agent))


# --- current inputs ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Inputs:
    """Everything the record is checked or computed against today. Read-only snapshot."""

    job_id: str | None
    jd_text: str | None
    career: Career
    lexicon: Lexicon
    rubric: Rubric
    markets: Markets
    now: str


def _text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def workspace_inputs(root: Path, now: str | None = None) -> Inputs:
    """The workspace-wide inputs (memory, lexicon, rubric, Markets); `for_job` adds a job's id and posting."""
    root = Path(root)
    profile = _text(root / "profile.md") or ""
    career = store.load_career(root)
    try:
        lexicon = store.lexicon_for(root, career)
    except WorkspaceError:          # an unusable career/lexicon.yaml is already an MEM001 error in `career`
        lexicon = load_lexicon(None)
    return Inputs(None, None, career, lexicon, rubric_mod.parse(profile), markets_mod.parse(profile),
                  now or models.utc_now())


def for_job(base: Inputs, job_dir: Path) -> Inputs:
    job_dir = Path(job_dir)
    job_id = None
    try:
        fm, _ = split_frontmatter((job_dir / "job.md").read_text(encoding="utf-8"))
        job_id = fm.get("id") if fm else None
    except (OSError, UnicodeDecodeError, WorkspaceError):
        pass
    return dataclasses.replace(base, job_id=job_id if isinstance(job_id, str) else None,
                               jd_text=_text(job_dir / JD_FILE))


def read_inputs(root: Path, job_dir: Path, now: str | None = None) -> Inputs:
    """The current inputs for the record in `job_dir`. Never writes; `now` defaults to `models.utc_now()`."""
    return for_job(workspace_inputs(root, now), job_dir)


# --- the agent half ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Agent:
    """The parsed agent half. Entries with errors are left out; `quotes` and `links` are gathered from every entry."""

    requirements: tuple[Requirement, ...] = ()
    fit: tuple[FitJudgement, ...] = ()
    job_facts: JobFacts = JobFacts()
    quotes: tuple[tuple[str, str], ...] = ()       # (where, quote) to verify against jd.md
    links: tuple[tuple[str, str], ...] = ()        # (requirement id, fact id)


class _Parser:
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.issues: list[Issue] = []
        self.quotes: list[tuple[str, str]] = []
        self.links: list[tuple[str, str]] = []

    def bad(self, code: str, message: str) -> None:
        self.issues.append(Issue("error", code, self.rel, message, _FIX))

    def mapping(self, value: object, where: str) -> dict | None:
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.bad("EVL003", f"{where} must be a mapping")
            return None
        return value

    def keys(self, value: dict, allowed: tuple[str, ...], where: str) -> None:
        unknown = sorted(str(k) for k in value if k not in allowed)
        if unknown:
            self.bad("EVL003", f"{where} has unknown key(s) {', '.join(unknown)}; allowed: {', '.join(allowed)}")

    def enum(self, cls, value: object, where: str, optional: bool = False):
        if optional and value is None:
            return None
        allowed = [m.value for m in cls]
        if isinstance(value, str) and value in allowed:
            return cls(value)
        self.bad("EVL003", f"{where} {value!r} is not one of {' | '.join(allowed)}")
        return False

    def text(self, value: object, where: str, required: bool, code: str = "EVL003") -> str | None | bool:
        if value is None and not required:
            return None
        if isinstance(value, str) and (value.strip() or not required):
            return value
        self.bad(code, f"{where} must be {'a non-empty' if required else 'a'} string, not {value!r}")
        return False

    def integer(self, value: object, where: str) -> int | None | bool:
        if value is None or type(value) is int:
            return value
        self.bad("EVL003", f"{where} must be a whole number, not {value!r}")
        return False

    def quote(self, value: object, where: str) -> None:
        if isinstance(value, str) and value.strip():
            self.quotes.append((where, value))
        else:
            self.bad("EVL004", f"{where} has no verbatim quote from jd.md")


def _requirement(p: _Parser, item: object, n: int, seen: set[str]) -> Requirement | None:
    where = f"requirements[{n}]"
    if not isinstance(item, dict):
        p.bad("EVL003", f"{where} must be a mapping")
        return None
    p.keys(item, _REQ_KEYS, where)
    rid = item.get("id")
    raw_links = item.get("links")
    if not isinstance(rid, str) or not rid.strip():
        p.bad("EVL002", f"{where} has no id (use r1, r2, ...)")
        rid = None
    elif rid in seen:
        p.bad("EVL002", f"requirement id {rid} is used more than once")
        rid = None
    else:
        seen.add(rid)
        where = rid
    before = len(p.issues)
    cls = p.enum(ReqClass, item.get("class"), f"{where}: class")
    kind = p.enum(Kind, item.get("kind"), f"{where}: kind")
    text = p.text(item.get("text"), f"{where}: text", required=True)
    term = p.text(item.get("term"), f"{where}: term", required=False)
    years = p.integer(item.get("years"), f"{where}: years")
    p.quote(item.get("quote"), f"{where}: quote")
    links: list[Link] = []
    if raw_links is not None and not isinstance(raw_links, list):
        p.bad("EVL003", f"{where}: links must be a list")
    for k, link in enumerate(raw_links or () if isinstance(raw_links, list) else (), 1):
        lwhere = f"{where}: links[{k}]"
        if not isinstance(link, dict):
            p.bad("EVL003", f"{lwhere} must be a mapping")
            continue
        p.keys(link, _LINK_KEYS, lwhere)
        fact = p.text(link.get("fact"), f"{lwhere}: fact", required=True)
        strength = p.enum(Strength, link.get("strength"), f"{lwhere}: strength")
        why = p.text(link.get("why"), f"{lwhere}: why", required=False)
        if isinstance(fact, str):
            p.links.append((rid or where, fact))
        if isinstance(fact, str) and strength is not False and why is not False:
            links.append(Link(fact, strength, why or ""))
    if rid is None or len(p.issues) != before:
        return None
    return Requirement(rid, cls, kind, text, item["quote"], term, years, tuple(links))


def _fit(p: _Parser, item: object, n: int, seen: set[str]) -> FitJudgement | None:
    where = f"fit[{n}]"
    if not isinstance(item, dict):
        p.bad("EVL003", f"{where} must be a mapping")
        return None
    p.keys(item, _FIT_KEYS, where)
    before = len(p.issues)
    row = p.text(item.get("row"), f"{where}: row", required=True)
    if isinstance(row, str):
        where = f"fit row {row.strip()!r}"
        if norm_label(row) in seen:
            p.bad("EVL003", f"{where} is judged more than once")
        seen.add(norm_label(row))
    judgement = p.enum(Judgement, _word(item.get("judgement")), f"{where}: judgement")
    quote = item.get("quote")
    if quote is None and judgement == Judgement.NO:
        pass                                   # the posting says nothing about this row
    elif quote is None or isinstance(quote, str):
        p.quote(quote, where)
    else:
        p.bad("EVL003", f"{where}: quote must be a string or null")
    if len(p.issues) != before:
        return None
    return FitJudgement(row, judgement, quote)


def _job_facts(p: _Parser, raw: object) -> JobFacts:
    facts = p.mapping(raw, "job_facts")
    if facts is None:
        return JobFacts()
    p.keys(facts, _FACTS_KEYS, "job_facts")
    market_raw = facts.get("market")
    market = p.text(market_raw, "job_facts.market", required=False)
    sponsor = p.mapping(facts.get("sponsorship"), "job_facts.sponsorship") or {}
    p.keys(sponsor, _SPONSOR_KEYS, "job_facts.sponsorship")
    needed_word = _word(sponsor.get("needed"))
    needed = None
    if needed_word in ("yes", "no"):
        needed = needed_word == "yes"
    elif needed_word is not None:
        p.bad("EVL003", f"job_facts.sponsorship.needed {sponsor.get('needed')!r} is not one of yes | no")
    listing_raw = sponsor.get("listing")
    listing = p.enum(Listing, "unknown" if listing_raw is None else listing_raw, "job_facts.sponsorship.listing")
    squote = sponsor.get("quote")
    if squote is not None and not isinstance(squote, str):
        p.bad("EVL003", "job_facts.sponsorship.quote must be a string")
        squote = None
    if listing not in (False, Listing.UNKNOWN):
        if squote is None or not squote.strip():
            p.bad("EVL006", f"job_facts.sponsorship.listing is {listing}, which needs a verbatim quote")
        else:
            p.quotes.append(("job_facts.sponsorship.quote", squote))
    comp = p.mapping(facts.get("compensation"), "job_facts.compensation")
    values: dict = {}
    floor_given = False
    if comp:
        p.keys(comp, _COMP_KEYS, "job_facts.compensation")
        missing = [k for k in ("source", "confidence", "as_of")
                   if not (isinstance(normalize_value(comp.get(k)), str) and normalize_value(comp.get(k)).strip())]
        if missing:
            p.bad("EVL006", f"job_facts.compensation needs source, confidence and as_of (missing: {', '.join(missing)})")
        if comp.get("source") is not None:
            values["source"] = p.text(comp.get("source"), "job_facts.compensation.source", required=False)
        values["comp_confidence"] = p.enum(SourceConfidence, comp.get("confidence"), "job_facts.compensation.confidence",
                                           optional=True)
        as_of = normalize_value(comp.get("as_of"))
        if as_of is not None:
            values["as_of"] = p.text(as_of, "job_facts.compensation.as_of", required=False)
        rng = p.mapping(comp.get("range"), "job_facts.compensation.range")
        if rng:
            p.keys(rng, _RANGE_KEYS, "job_facts.compensation.range")
            values.update(range_low=p.integer(rng.get("low"), "job_facts.compensation.range.low"),
                          range_high=p.integer(rng.get("high"), "job_facts.compensation.range.high"),
                          range_currency=p.text(rng.get("currency"), "job_facts.compensation.range.currency", False),
                          range_basis=p.enum(MarketBasis, rng.get("basis"), "job_facts.compensation.range.basis", True))
        floor = p.mapping(comp.get("floor"), "job_facts.compensation.floor")
        if floor:
            p.keys(floor, _FLOOR_KEYS, "job_facts.compensation.floor")
            floor_given = floor.get("amount") is not None or floor.get("currency") is not None
            values.update(floor_amount=p.integer(floor.get("amount"), "job_facts.compensation.floor.amount"),
                          floor_currency=p.text(floor.get("currency"), "job_facts.compensation.floor.currency", False),
                          floor_basis=p.enum(MarketBasis, floor.get("basis"), "job_facts.compensation.floor.basis", True))
    no_market = market_raw is None or (isinstance(market_raw, str) and not market_raw.strip())
    if no_market and (floor_given or needed is not None):
        p.bad("EVL006", "a floor or sponsorship need is given without job_facts.market (name the profile Markets row)")
    values = {k: v for k, v in values.items() if v is not False}
    return JobFacts(market=market or None, needed=needed, listing=listing or Listing.UNKNOWN,
                    sponsorship_quote=squote, **values)


def parse_agent(rec: Record) -> tuple[Agent, list[Issue]]:
    """The agent half, best effort, with its structural issues (EVL002, EVL003, EVL004 for absent quotes, EVL006)."""
    p = _Parser(rec.rel)
    agent = rec.agent if isinstance(rec.agent, dict) else {}
    p.keys(agent, _AGENT_KEYS, "agent")
    reqs: list[Requirement] = []
    items = agent.get("requirements")
    if items is not None and not isinstance(items, list):
        p.bad("EVL003", "agent.requirements must be a list")
    seen: set[str] = set()
    for n, item in enumerate(items if isinstance(items, list) else (), 1):
        req = _requirement(p, item, n, seen)
        if req is not None:
            reqs.append(req)
    fit: list[FitJudgement] = []
    rows = agent.get("fit")
    if rows is not None and not isinstance(rows, list):
        p.bad("EVL003", "agent.fit must be a list")
    seen_rows: set[str] = set()
    for n, item in enumerate(rows if isinstance(rows, list) else (), 1):
        judged = _fit(p, item, n, seen_rows)
        if judged is not None:
            fit.append(judged)
    job_facts = _job_facts(p, agent.get("job_facts"))
    return Agent(tuple(reqs), tuple(fit), job_facts, tuple(p.quotes), tuple(p.links)), p.issues


def validate_agent(rec: Record, jd_text: str | None, career: Career, rubric: Rubric, markets: Markets,
                   job_id: str | None) -> list[Issue]:
    """EVL002-EVL006 for the agent half (spec 8 and 16.1). Pure; never raises on a malformed record.

    `job_id` is the id in this job's `job.md` (None when it cannot be read: the job check is then skipped).
    """
    agent, issues = parse_agent(rec)

    def bad(code: str, message: str) -> None:
        issues.append(Issue("error", code, rec.rel, message, _FIX))

    if not isinstance(rec.job, str) or (job_id is not None and rec.job != job_id):
        bad("EVL002", f"the record's job is {rec.job!r}, not this job ({job_id})")
    keys = {row.key for row in rubric.rows}
    for judged in agent.fit:
        if norm_label(judged.row) not in keys:
            bad("EVL003", f"fit row {judged.row.strip()!r} is not a fit row of the profile's Scoring rubric")
    if jd_text is None:
        if agent.quotes:
            bad("EVL004", f"{JD_FILE} is missing or unreadable, so no quote can be verified; save the posting verbatim")
    else:
        posting = normalize_text(jd_text)
        for where, quote in agent.quotes:
            wanted = normalize_text(quote)
            if not wanted or wanted not in posting:
                bad("EVL004", f"{where}: {quote!r} is not found verbatim in {JD_FILE}")
    usable = derive._usable(career)
    by_id = career.by_id()
    for rid, fact in agent.links:
        if fact not in usable:
            state = ("missing" if fact not in by_id else by_id[fact].status if by_id[fact].status not in store.ACTIVE
                     else "its parent is not active")
            bad("EVL005", f"{rid}: link to {fact!r} is not an active fact ({state})")
    try:
        markets_mod.verify_market(agent.job_facts, markets)
    except MarketError as exc:
        bad("EVL006", str(exc))
    return issues


# --- the result ----------------------------------------------------------------------------------

def _natural(rid: str) -> tuple:
    return score._natural(rid)


def display_requirements(derived: derive.Derived) -> list[dict]:
    """`result.requirements`: per requirement in natural id order, its outcome, facts, weakest status and reason."""
    out = []
    for d in sorted(derived.items, key=lambda d: _natural(d.id)):
        entry = {"id": d.id, "outcome": str(d.outcome), "facts": list(d.facts), "status_floor": d.status_floor}
        if d.reason is not None:
            entry["reason"] = d.reason
        out.append(entry)
    return out


def assemble(basis: dict, stamps: Stamps | dict, requirements: list[dict]) -> dict:
    """The `result:` mapping for a basis: scored with the rules version the basis declares (`score.score`)."""
    s = score.score(basis)
    stamp_map = dataclasses.asdict(stamps) if isinstance(stamps, Stamps) else dict(stamps)
    return {
        "tier": str(s.tier),
        "combined": score.stored_score(s.combined),
        "coverage": score.stored_score(s.coverage),
        "fit": score.stored_score(s.fit),
        "confidence": str(s.confidence),
        "caps": [{"code": c.code, "ceiling": str(c.ceiling), "reason": c.reason} for c in s.caps],
        "action_items": [score.action_item_text(i) for i in s.action_items],
        "requirements": [dict(r) for r in requirements],
        "stamps": {k: stamp_map[k] for k in STAMP_KEYS},
        "basis_sha256": sha256_hex(canonical_json(basis)),
        "basis": basis,
    }


def derive_basis(agent: Agent, inputs: Inputs) -> tuple[dict, derive.Derived]:
    """The current basis (current versions) and derivation. Raises `DeriveError`/`MarketError` on invalid links/market."""
    derived = derive.derive(agent.requirements, inputs.career, inputs.lexicon, inputs.now)
    check = markets_mod.verify_market(agent.job_facts, inputs.markets)
    basis = derived.basis.complete(agent.fit, agent.job_facts, check, inputs.rubric)
    basis["basis_version"], basis["rules_version"] = rules.BASIS_VERSION, rules.RULES_VERSION
    return basis, derived


def compute(rec: Record, inputs: Inputs) -> dict:
    """The fresh `result:` for a record whose agent half passed `validate_agent`; `RecordError` if it does not parse."""
    agent, issues = parse_agent(rec)
    if issues:
        raise RecordError(f"{rec.rel}: the agent half is invalid ({', '.join(sorted({i.code for i in issues}))})",
                          code=issues[0].code)
    if inputs.jd_text is None:
        raise RecordError(f"{rec.rel}: {JD_FILE} is missing", code="EVL004")
    basis, derived = derive_basis(agent, inputs)
    current = stamps(inputs.jd_text, inputs.career, inputs.rubric, rules.RULES_VERSION, rec.fm.get("agent"),
                     lexicon=inputs.lexicon, markets=inputs.markets, now=inputs.now)
    return assemble(basis, current, display_requirements(derived))


def to_bytes(rec: Record, result: dict) -> bytes:
    """The record file with `result:` replaced and the body re-rendered (the frontmatter is otherwise as loaded)."""
    fm = {k: v for k, v in rec.fm.items() if k != "result"}
    fm["result"] = result
    return join_frontmatter(fm, render_body(result)).encode("utf-8")


# --- the body ------------------------------------------------------------------------------------

_CELL = re.compile(r"\s+")


def _cell(value: object) -> str:
    return _CELL.sub(" ", "" if value is None else str(value)).strip().replace("|", "\\|")


def _line(value: object) -> str:
    return " ".join(str(value).split())


def _score_text(value: object) -> str:
    return "not scored" if value is None else fixed4(value)


def render_body(result: dict) -> str:
    """The body for a result: a fixed template of stored values only (no timestamp, no randomness).

    The stored body must equal this byte for byte; a difference is tampering (EVL007, "body"). Raises on a malformed
    result (callers checking a hand-edited file catch that and report it).
    """
    lines = ["", "# Evaluation", "",
             "<!-- Generated by `careeros evaluate` from `result:`. Do not edit: a changed body is reported as "
             "tampering (EVL007). -->", "",
             f"- **Tier:** {_cell(result['tier'])}",
             f"- **Combined score:** {_score_text(result['combined'])}",
             f"- **Coverage:** {_score_text(result['coverage'])}",
             f"- **Fit:** {_score_text(result['fit'])}",
             f"- **Confidence:** {_cell(result['confidence'])}", "",
             "## Requirements", ""]
    requirements = list(result["requirements"])
    if requirements:
        lines += ["| Id | Outcome | Facts | Weakest status | Note |", "|---|---|---|---|---|"]
        for r in requirements:
            lines.append(f"| {_cell(r['id'])} | {_cell(r['outcome'])} | {_cell(', '.join(r['facts']))} | "
                         f"{_cell(r['status_floor'])} | {_cell(r.get('reason'))} |")
    else:
        lines.append("No requirements were extracted.")
    lines += ["", "## Caps", ""]
    lines += [f"- {_line(c['code'])} (at most {_line(c['ceiling'])}): {_line(c['reason'])}"
              for c in result["caps"]] or ["- none"]
    lines += ["", "## What would change this", ""]
    lines += [f"- {_line(item)}" for item in result["action_items"]] or ["- nothing"]
    lines.append("")
    return "\n".join(lines)
