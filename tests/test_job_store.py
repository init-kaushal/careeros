from careeros.core.job_store import JobStore
from careeros.core.models import Job, Sighting
from careeros.storage.filesystem import LocalFilesystemStorage

_NOW = "2026-09-20T00:00:00+00:00"
_LATER = "2026-09-21T00:00:00+00:00"


def _job(**over):
    base = dict(
        id="acme-sre-aaaa", source="linkedin", company="Acme", title="Senior SRE",
        url="https://linkedin.test/1", created_at=_NOW, updated_at=_NOW,
    )
    base.update(over)
    return Job(**base)


def _store(tmp_path):
    return JobStore(LocalFilesystemStorage(str(tmp_path)))


def test_save_new_creates_when_no_match_exists(tmp_path):
    out = _store(tmp_path).save_new(_job())
    assert out.created is True
    assert out.enriched == ()
    assert Job.load(LocalFilesystemStorage(str(tmp_path)), out.job.id).title == "Senior SRE"


def test_save_new_merges_a_title_variant_instead_of_duplicating(tmp_path):
    store = _store(tmp_path)
    first = store.save_new(_job())
    second = store.save_new(_job(
        id="acme-sre-bbbb", source="greenhouse", title="Senior Site Reliability Engineer",
        url="https://greenhouse.test/9",
    ))
    assert second.created is False
    assert second.job.id == first.job.id
    assert len(Job.list_all(LocalFilesystemStorage(str(tmp_path)))) == 1


def test_merge_enriches_only_absent_fields(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job(description=None, location=None))
    out = store.save_new(_job(
        id="b", url="https://greenhouse.test/9",
        description="full jd", location="SF",
    ))
    assert out.created is False
    assert out.job.description == "full jd"
    assert out.job.location == "SF"
    assert set(out.enriched) == {"description", "location"}


def test_merge_never_overwrites_a_populated_field(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job(description="original"))
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9",
                              description="replacement"))
    assert out.job.description == "original"
    assert "description" not in out.enriched


def test_merge_does_not_treat_remote_false_as_absent(tmp_path):
    # Truthiness bug guard: `if not existing.remote` would overwrite False.
    store = _store(tmp_path)
    store.save_new(_job(remote=False))
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9", remote=True))
    assert out.job.remote is False
    assert "remote" not in out.enriched


def test_merge_does_not_treat_salary_zero_as_absent(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job(salary_min=0))
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9", salary_min=200000))
    assert out.job.salary_min == 0
    assert "salary_min" not in out.enriched


def test_merge_never_alters_user_state(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    store = JobStore(storage)
    created = store.save_new(_job())
    advanced = created.job.model_copy(update={
        "stage": "interviewing", "applied_at": _NOW, "notes": ["spoke to recruiter"],
    })
    advanced.save(storage)

    out = store.save_new(_job(id="b", url="https://greenhouse.test/9",
                              stage="saved", applied_at=None, notes=[]))
    assert out.created is False
    assert out.job.stage == "interviewing"
    assert out.job.applied_at == _NOW
    assert out.job.notes == ["spoke to recruiter"]


def test_first_merge_seeds_sightings_from_the_existing_record(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job())
    out = store.save_new(_job(id="b", source="greenhouse",
                              url="https://greenhouse.test/9"))
    assert [s.source for s in out.job.sightings] == ["linkedin", "greenhouse"]
    assert out.job.sightings[0].url == "https://linkedin.test/1"
    assert out.job.sightings[1].url == "https://greenhouse.test/9"


def test_second_merge_appends_without_reseeding(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job())
    store.save_new(_job(id="b", source="greenhouse", url="https://greenhouse.test/9"))
    out = store.save_new(_job(id="c", source="wellfound", url="https://wf.test/3"))
    assert [s.source for s in out.job.sightings] == ["linkedin", "greenhouse", "wellfound"]


def test_force_creates_a_duplicate(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job())
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9"), force=True)
    assert out.created is True
    assert len(Job.list_all(LocalFilesystemStorage(str(tmp_path)))) == 2


def test_updated_at_unchanged_when_nothing_was_enriched(tmp_path):
    store = _store(tmp_path)
    store.save_new(_job())
    out = store.save_new(_job(id="b", url="https://greenhouse.test/9"))
    assert out.enriched == ()
    assert out.job.updated_at == _NOW


def test_job_json_without_a_sightings_key_still_loads(tmp_path):
    # A pre-Phase-10 job file has no "sightings" key at all. The spec's
    # "no migration needed" claim depends on this loading cleanly.
    import json
    storage = LocalFilesystemStorage(str(tmp_path))
    legacy = {
        "id": "old", "source": "manual", "company": "Acme", "title": "Senior SRE",
        "stage": "saved", "created_at": _NOW, "updated_at": _NOW,
        "requirements": [], "notes": [], "currency": "USD",
    }
    assert "sightings" not in legacy
    storage.atomic_write("jobs/old.json", json.dumps(legacy).encode())
    loaded = Job.load(storage, "old")
    assert loaded.sightings == []


def test_sighting_is_a_pydantic_model():
    s = Sighting(source="greenhouse", url="https://x.test/1", seen_at=_NOW)
    assert s.model_dump()["source"] == "greenhouse"
