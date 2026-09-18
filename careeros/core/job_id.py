import os
import re


def make_job_id(company: str, title: str) -> str:
    def slugify(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")

    company_slug = slugify(company)[:12]
    title_slug = slugify(title)[:16]
    suffix = os.urandom(2).hex()
    parts = [p for p in [company_slug, title_slug] if p]
    base = "-".join(parts)[:28].rstrip("-")
    return f"{base}-{suffix}"
