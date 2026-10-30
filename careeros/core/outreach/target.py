"""The target-company record `targets/<slug>.md` (spec 4.2): strict parsing and the canonical bytes. Pure.

Same loader and `OUT001` contract as `person`. `slug` is unique and must already be in `slugify` form (it need not equal
`slugify(company)`); `fit` is an integer 0 to 10; every sponsorship value except `n/a` needs at least one evidence entry
with a non-empty `source` and an ISO `read_on` date (unverified is acceptable, a guess is not).
"""

from __future__ import annotations

from careeros.core import ids
from careeros.core.models import Issue
from careeros.core.outreach import _common as c
from careeros.core.outreach import rules
from careeros.core.outreach.model import Evidence, Target
from careeros.core.outreach.slug import slugify
from careeros.core.workspace import join_frontmatter

DEFAULT_BODY = "\nFree notes (ignored by code).\n"
_REQUIRED = ("schema", "id", "slug", "company", "market", "fit", "sponsorship")
_OPTIONAL = ("evidence",)
_EVIDENCE_KEYS = {"source", "read_on"}


def _evidence(raw: object, problems: list[str]) -> tuple[Evidence, ...] | None:
    if not isinstance(raw, list):
        problems.append("evidence must be a list of {source, read_on} entries")
        return None
    if len(raw) > c.MAX_LIST:
        problems.append(f"evidence has more than {c.MAX_LIST} entries")
        return None
    out, ok = [], True
    for n, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) != _EVIDENCE_KEYS:
            problems.append(f"evidence entry {n} must be a mapping with exactly source and read_on")
            ok = False
            continue
        local: list[str] = []
        source = c.text_value(entry["source"], f"evidence entry {n} source", local)
        read_on = c.date_value(entry["read_on"], f"evidence entry {n} read_on", local)
        problems.extend(local)
        if local:
            ok = False
        else:
            out.append(Evidence(source, read_on))
    return tuple(out) if ok else None


def _validate(fm: dict, rel: str) -> tuple[Target | None, list[str]]:
    problems = c.keys_problems(fm, _REQUIRED, _OPTIONAL)
    if (p := c.schema_problem(fm)) is not None:
        problems.append(p)
    tgt_id = fm.get("id")
    if not ids.is_valid_id("target", tgt_id):
        problems.append(f"id {tgt_id!r} is not a tgt_ id")
    slug = fm.get("slug")
    if not isinstance(slug, str) or slugify(slug) != slug:
        problems.append(f"slug {slug!r} must be lower-case ASCII letters and digits separated by single hyphens")
    elif (p := c.file_name_problem(rel, f"{slug}.md")) is not None:
        problems.append(f"slug {slug!r}: {p}")
    company = c.text_value(fm.get("company"), "company", problems)
    market = c.text_value(fm.get("market"), "market", problems)
    fit = fm.get("fit")
    if type(fit) is not int or not 0 <= fit <= 10:
        problems.append("fit must be a whole number from 0 to 10")
    sponsorship = fm.get("sponsorship")
    if sponsorship not in rules.SPONSORSHIP:
        problems.append(f"sponsorship must be one of {', '.join(rules.SPONSORSHIP)}")
    evidence = _evidence(fm.get("evidence", []), problems)
    if evidence is not None and sponsorship in rules.SPONSORSHIP and sponsorship != rules.SPONSORSHIP_NA and not evidence:
        problems.append(f"sponsorship {sponsorship} needs at least one evidence entry with a source and a read_on date")
    if problems or None in (company, market, evidence):
        return None, problems
    return Target(tgt_id, slug, company, market, fit, sponsorship, evidence), problems


def parse(text: str, rel: str) -> tuple[Target | None, list[Issue]]:
    """`(target, [])` or `(None, [OUT001 issues])`. Never raises."""
    fm, problems = c.load(text, rel)
    parsed = None
    if fm is not None:
        try:
            parsed, problems = _validate(fm, rel)
        except Exception as exc:  # noqa: BLE001 - hostile input must never escape as an exception
            parsed, problems = None, [f"{rel} cannot be read: {type(exc).__name__}"]
    if parsed is not None:
        return parsed, []
    return None, c.issues(rel, problems)


def to_bytes(record: Target, body: str = DEFAULT_BODY) -> bytes:
    """The exact bytes of the file: frontmatter in the documented key order, then `body` (LF line endings)."""
    fm = {"schema": rules.SCHEMA, "id": record.id, "slug": record.slug, "company": record.company, "market": record.market,
          "fit": record.fit, "sponsorship": record.sponsorship,
          "evidence": [{"source": e.source, "read_on": e.read_on} for e in record.evidence]}
    return join_frontmatter(fm, body.replace("\r\n", "\n").replace("\r", "\n")).encode("utf-8")
