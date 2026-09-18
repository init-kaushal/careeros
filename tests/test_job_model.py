import pytest
from datetime import datetime, timezone
from careeros.core.models import Job, JOB_STAGES
from careeros.core.job_id import make_job_id
from careeros.storage.filesystem import LocalFilesystemStorage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_job(**kwargs) -> Job:
    defaults = dict(
        id="acme-sre-ab12",
        source="manual",
        company="Acme",
        title="Senior SRE",
        stage="saved",
        created_at=_now(),
        updated_at=_now(),
    )
    defaults.update(kwargs)
    return Job(**defaults)


def test_job_save_writes_file(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    job = _make_job()
    job.save(storage)
    assert (tmp_path / "jobs" / "acme-sre-ab12.json").exists()


def test_job_load_round_trips(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    job = _make_job(company="Stripe", title="Staff Engineer", url="https://stripe.com/jobs/1")
    job.save(storage)
    loaded = Job.load(storage, job.id)
    assert loaded.company == "Stripe"
    assert loaded.title == "Staff Engineer"
    assert loaded.url == "https://stripe.com/jobs/1"


def test_job_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        Job.load(storage, "nonexistent-id")


def test_list_all_empty(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    assert Job.list_all(storage) == []


def test_list_all_sorted_by_created_at_desc(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    j1 = _make_job(id="job-1", created_at="2026-01-01T00:00:00+00:00", updated_at=_now())
    j2 = _make_job(id="job-2", created_at="2026-06-01T00:00:00+00:00", updated_at=_now())
    j1.save(storage)
    j2.save(storage)
    result = Job.list_all(storage)
    assert result[0].id == "job-2"
    assert result[1].id == "job-1"


def test_description_capped_at_4000_chars(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    job = _make_job(description="x" * 5000)
    job.save(storage)
    loaded = Job.load(storage, job.id)
    assert len(loaded.description) == 4000


def test_job_stages_has_five_values():
    assert set(JOB_STAGES) == {"saved", "applied", "interviewing", "offer", "closed"}
    assert len(JOB_STAGES) == 5


def test_make_job_id_format():
    job_id = make_job_id("Acme Corp", "Senior SRE")
    parts = job_id.rsplit("-", 1)
    assert len(parts) == 2
    assert len(parts[1]) == 4
    assert all(c in "0123456789abcdef" for c in parts[1])


def test_make_job_id_unique():
    id1 = make_job_id("Acme", "SRE")
    id2 = make_job_id("Acme", "SRE")
    assert id1 != id2


def test_make_job_id_slugified():
    job_id = make_job_id("Acme Corp!", "Senior SRE / DevOps")
    prefix = job_id.rsplit("-", 1)[0]
    assert prefix == prefix.lower()
    assert all(c.isalnum() or c == "-" for c in prefix)
