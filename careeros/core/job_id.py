import secrets

from careeros.core.ids import slugify


def make_job_id(company: str, title: str) -> str:
    company_slug = slugify(company)[:12]
    title_slug = slugify(title)[:16]
    suffix = secrets.token_hex(2)
    parts = [p for p in [company_slug, title_slug] if p]
    base = "-".join(parts)[:28].rstrip("-")
    return f"{base}-{suffix}"
