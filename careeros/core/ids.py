import re
import secrets


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def make_company_id(name: str) -> str:
    return slugify(name)[:40]


def make_person_id(name: str, company_id: str) -> str:
    return (company_id + "-" + slugify(name))[:60]


def make_compensation_id(company: str, role: str) -> str:
    company_slug = slugify(company)[:20]
    role_slug = slugify(role)[:20]
    suffix = secrets.token_hex(3)
    parts = [p for p in [company_slug, role_slug] if p]
    return "-".join(parts) + "-" + suffix


def make_approval_id(action: str, entity_id: str | None) -> str:
    # Both components become a path segment under approvals/, so they are
    # slugified for the same reason outreach message IDs are: an arbitrary
    # entity_id must never reach storage._resolve() as a raw path component.
    # The random suffix keeps two proposals for the same entity distinct, so
    # the older one stays readable in its superseded state for the audit trail.
    parts = [p for p in [slugify(action)[:20], slugify(entity_id or "")[:30]] if p]
    return "-".join(parts) + "-" + secrets.token_hex(3)
