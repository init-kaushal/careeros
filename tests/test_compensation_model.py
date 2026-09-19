import pytest
from careeros.core.ids import make_compensation_id
from careeros.core.models import CompensationDataPoint
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    comp_id = make_compensation_id("Acme Corp", "Senior SRE")
    point = CompensationDataPoint(
        id=comp_id, job_id="acme-sre-abc1", role="Senior SRE", seniority="senior",
        geo="San Francisco, CA", company="Acme Corp", base_min=180000, base_max=220000,
        bonus="10-15%", equity="0.01-0.05%", source_url="https://www.levels.fyi/companies/acme-corp/salaries/senior-sre",
        confidence="medium", researched_at="2026-09-19T00:00:00Z",
    )
    point.save(storage)
    loaded = CompensationDataPoint.load(storage, comp_id)
    assert loaded.base_min == 180000
    assert loaded.base_max == 220000
    assert loaded.confidence == "medium"
    assert loaded.source == "levels.fyi"


def test_defaults(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    comp_id = make_compensation_id("Acme Corp", "Senior SRE")
    point = CompensationDataPoint(
        id=comp_id, job_id="acme-sre-abc1", role="Senior SRE", researched_at="2026-09-19T00:00:00Z",
    )
    assert point.currency == "USD"
    assert point.confidence == "low"
    assert point.source == "levels.fyi"
    assert point.seniority is None
    assert point.base_min is None


def test_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        CompensationDataPoint.load(storage, "nonexistent")
