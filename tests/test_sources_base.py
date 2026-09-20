import dataclasses

import pytest

from careeros.sources.base import Posting, job_from_posting, posting_from_scrape


def _posting(**over):
    base = dict(
        source="greenhouse", title="Senior SRE", company="Acme",
        url="https://boards.greenhouse.io/acme/jobs/1",
    )
    base.update(over)
    return Posting(**base)


def test_posting_is_frozen():
    p = _posting()
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.title = "tampered"


def test_posting_optional_fields_default_to_none():
    p = _posting()
    assert p.location is None
    assert p.description is None
    assert p.source_id is None


def test_job_from_posting_maps_every_field():
    p = _posting(location="SF", description="jd text", source_id="42")
    job = job_from_posting(p, "2026-09-20T00:00:00+00:00")
    assert job.source == "greenhouse"
    assert job.title == "Senior SRE"
    assert job.company == "Acme"
    assert job.url == "https://boards.greenhouse.io/acme/jobs/1"
    assert job.location == "SF"
    assert job.description == "jd text"
    assert job.source_id == "42"
    assert job.stage == "saved"
    assert job.created_at == "2026-09-20T00:00:00+00:00"
    assert job.updated_at == "2026-09-20T00:00:00+00:00"


def test_job_from_posting_ids_are_not_stable():
    # make_job_id is random-suffixed, so the id is NOT an identity. This is
    # why dedup compares fields, never ids, and why JobStore must run before
    # an id is persisted.
    p = _posting()
    a = job_from_posting(p, "2026-09-20T00:00:00+00:00")
    b = job_from_posting(p, "2026-09-20T00:00:00+00:00")
    assert a.id != b.id


def test_posting_from_scrape_maps_scraper_dict():
    raw = {
        "source_board": "linkedin", "title": "Staff Engineer", "company": "Beta",
        "location": "Remote", "url": "https://www.linkedin.com/jobs/view/1",
    }
    p = posting_from_scrape(raw)
    assert p.source == "linkedin"
    assert p.title == "Staff Engineer"
    assert p.company == "Beta"
    assert p.location == "Remote"
    assert p.url == "https://www.linkedin.com/jobs/view/1"


def test_posting_from_scrape_rejects_missing_company():
    # company is half the dedup fingerprint; defaulting it to "" would
    # silently merge unrelated postings from sources that omit it.
    raw = {"source_board": "linkedin", "title": "Staff Engineer",
           "url": "https://x.test/1", "company": ""}
    with pytest.raises(ValueError, match="company"):
        posting_from_scrape(raw)
