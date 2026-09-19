import pytest
from careeros.core.ids import make_company_id, make_person_id
from careeros.core.models import Company, OutreachMessage, Person
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_company_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    company_id = make_company_id("Acme Corp")
    company = Company(id=company_id, name="Acme Corp", industry="Software", researched_at="2026-09-19T00:00:00Z")
    company.save(storage)
    loaded = Company.load(storage, company_id)
    assert loaded.name == "Acme Corp"
    assert loaded.industry == "Software"


def test_company_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        Company.load(storage, "nonexistent")


def test_person_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    company_id = make_company_id("Acme Corp")
    person_id = make_person_id("Jane Doe", company_id)
    person = Person(
        id=person_id, company_id=company_id, name="Jane Doe",
        role_category="em", title="Engineering Manager", researched_at="2026-09-19T00:00:00Z",
    )
    person.save(storage)
    loaded = Person.load(storage, person_id)
    assert loaded.name == "Jane Doe"
    assert loaded.role_category == "em"
    assert loaded.email is None


def test_person_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        Person.load(storage, "nonexistent")


def test_outreach_message_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    message_id = "acme-sre-abc1__acme-corp-jane-doe"
    message = OutreachMessage(
        id=message_id, job_id="acme-sre-abc1", person_id="acme-corp-jane-doe",
        draft_text="Hello Jane...", created_at="2026-09-19T00:00:00Z",
    )
    message.save(storage)
    loaded = OutreachMessage.load(storage, message_id)
    assert loaded.draft_text == "Hello Jane..."
    assert loaded.send_state == "drafted"
    assert loaded.referral_state == "research"


def test_outreach_message_load_missing_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        OutreachMessage.load(storage, "nonexistent")
