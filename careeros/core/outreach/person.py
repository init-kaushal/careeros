"""The person record `people/<per_id>.md` (spec 4.1): strict parsing and the canonical bytes. Pure.

`parse` reads the file with the strict frontmatter loader (aliases, merge keys and duplicate keys refused) and reports
every shape problem as `OUT001`; it never raises and repairs nothing. `id` and `url` are identity-bearing: `url` must be
the canonical form `approvals.item.canonical_profile_url` returns, and `company_slug` must equal `slugify(company)`. The
record holds no state; `found_in`, `role`, `company_slug` and `market` are editable source data.
"""

from __future__ import annotations

from careeros.core import ids
from careeros.core.approvals import item
from careeros.core.models import Issue
from careeros.core.outreach import _common as c
from careeros.core.outreach import rules
from careeros.core.outreach.model import FoundIn, Person
from careeros.core.outreach.slug import slugify
from careeros.core.workspace import join_frontmatter

DEFAULT_BODY = "\nFree notes (ignored by code).\n"
_REQUIRED = ("schema", "id", "url", "name", "headline", "company", "company_slug", "role", "found_in", "found_at")
_OPTIONAL = ("market",)
_FOUND_KEYS = {"kind", "id"}


def _found_in(raw: object, problems: list[str]) -> tuple[FoundIn, ...] | None:
    if not isinstance(raw, list):
        problems.append("found_in must be a list of {kind, id} entries")
        return None
    if len(raw) > c.MAX_LIST:
        problems.append(f"found_in has more than {c.MAX_LIST} entries")
        return None
    out, ok = [], True
    for n, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) != _FOUND_KEYS:
            problems.append(f"found_in entry {n} must be a mapping with exactly kind and id")
            ok = False
            continue
        kind, ident = entry["kind"], entry["id"]
        if kind not in rules.FOUND_IN_KINDS:
            problems.append(f"found_in entry {n}: kind must be one of {', '.join(rules.FOUND_IN_KINDS)}")
            ok = False
        elif not ids.is_valid_id(kind, ident):
            problems.append(f"found_in entry {n}: id {ident!r} is not a {kind} id")
            ok = False
        else:
            if FoundIn(kind, ident) in out:
                problems.append(f"found_in entry {n}: {kind} {ident} is listed more than once")
                ok = False
            out.append(FoundIn(kind, ident))
    return tuple(out) if ok else None


def _validate(fm: dict, rel: str) -> tuple[Person | None, list[str]]:
    problems = c.keys_problems(fm, _REQUIRED, _OPTIONAL)
    if (p := c.schema_problem(fm)) is not None:
        problems.append(p)
    per_id = fm.get("id")
    if not ids.is_valid_id("person", per_id):
        problems.append(f"id {per_id!r} is not a per_ id")
    elif (p := c.file_name_problem(rel, f"{per_id}.md")) is not None:
        problems.append(f"id {per_id!r}: {p}")
    url = fm.get("url")
    if not isinstance(url, str) or item.canonical_profile_url(url) != url:
        problems.append(f"url {url!r} must be a canonical LinkedIn profile URL "
                        f"(https://www.linkedin.com/in/<slug>; try {item.canonical_profile_url(url)!r})")
    name = c.text_value(fm.get("name"), "name", problems)
    headline = c.text_value(fm.get("headline"), "headline", problems, allow_empty=True)
    company = c.text_value(fm.get("company"), "company", problems)
    company_slug = fm.get("company_slug")
    if company is not None:
        expected = slugify(company)
        if expected is None:
            problems.append("company has no ASCII letter or digit, so it has no company_slug; use an ASCII spelling")
        elif company_slug != expected:
            problems.append(f"company_slug {company_slug!r} must equal slugify(company), which is {expected!r}")
    role = fm.get("role")
    if role not in rules.ROLES:
        problems.append(f"role must be one of {', '.join(rules.ROLES)}")
    market = None
    if "market" in fm:
        market = c.text_value(fm["market"], "market", problems)
    found_in = _found_in(fm.get("found_in"), problems)
    found_at = c.date_value(fm.get("found_at"), "found_at", problems)
    if problems or None in (name, headline, company, found_in, found_at) or ("market" in fm and market is None):
        return None, problems
    return Person(per_id, url, name, headline, company, company_slug, role, market, found_in, found_at), problems


def parse(text: str, rel: str) -> tuple[Person | None, list[Issue]]:
    """`(person, [])` or `(None, [OUT001 issues])`. Never raises."""
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


def to_bytes(record: Person, body: str = DEFAULT_BODY) -> bytes:
    """The exact bytes of the file: frontmatter in the documented key order, then `body` (LF line endings)."""
    fm: dict = {"schema": rules.SCHEMA, "id": record.id, "url": record.url, "name": record.name, "headline": record.headline,
                "company": record.company, "company_slug": record.company_slug, "role": record.role}
    if record.market is not None:
        fm["market"] = record.market
    fm["found_in"] = [{"kind": f.kind, "id": f.id} for f in record.found_in]
    fm["found_at"] = record.found_at
    return join_frontmatter(fm, body.replace("\r\n", "\n").replace("\r", "\n")).encode("utf-8")
