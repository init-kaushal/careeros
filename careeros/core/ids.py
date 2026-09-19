import re


def _slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def make_company_id(name: str) -> str:
    return _slugify(name)[:40]


def make_person_id(name: str, company_id: str) -> str:
    return (company_id + "-" + _slugify(name))[:60]
