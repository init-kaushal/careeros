from pathlib import Path

import pytest
from pydantic import ValidationError

from careeros.core.models import (
    ConnectionRequest, Goals, OutreachMessage, Preferences, Profile, Skill, Skills,
)
from careeros.operations.outreach import make_message_id


def test_profile_save_and_load(tmp_workspace):
    storage = tmp_workspace.storage
    profile = Profile(name="Alice Johnson", title="Senior SRE", years_of_experience=8)
    profile.save(storage)
    loaded = Profile.load(storage)
    assert loaded.name == "Alice Johnson"
    assert loaded.title == "Senior SRE"
    assert loaded.years_of_experience == 8


def test_profile_load_missing_raises(tmp_workspace):
    with pytest.raises(FileNotFoundError):
        Profile.load(tmp_workspace.storage)


def test_profile_load_or_empty_returns_defaults(tmp_workspace):
    profile = Profile.load_or_empty(tmp_workspace.storage)
    assert profile.name == ""
    assert profile.email is None


def test_profile_optional_fields_default_none(tmp_workspace):
    profile = Profile(name="Bob")
    profile.save(tmp_workspace.storage)
    loaded = Profile.load(tmp_workspace.storage)
    assert loaded.email is None
    assert loaded.title is None


def test_skills_save_and_load(tmp_workspace):
    storage = tmp_workspace.storage
    skills = Skills(skills=[Skill(name="Python", level="expert", source="resume")])
    skills.save(storage)
    loaded = Skills.load_or_empty(storage)
    assert len(loaded.skills) == 1
    assert loaded.skills[0].name == "Python"
    assert loaded.skills[0].source == "resume"


def test_skills_load_or_empty_when_missing(tmp_workspace):
    result = Skills.load_or_empty(tmp_workspace.storage)
    assert result.skills == []


def test_preferences_round_trip(tmp_workspace):
    storage = tmp_workspace.storage
    prefs = Preferences(
        target_roles=["SRE", "Platform Engineer"],
        minimum_compensation=150000,
        remote_preference="remote",
    )
    prefs.save(storage)
    loaded = Preferences.load_or_empty(storage)
    assert loaded.target_roles == ["SRE", "Platform Engineer"]
    assert loaded.minimum_compensation == 150000
    assert loaded.remote_preference == "remote"


def test_preferences_defaults(tmp_workspace):
    prefs = Preferences.load_or_empty(tmp_workspace.storage)
    assert prefs.target_roles == []
    assert prefs.visa_sponsorship_required is False
    assert prefs.compensation_currency == "USD"


def test_goals_round_trip(tmp_workspace):
    storage = tmp_workspace.storage
    goals = Goals(
        short_term=["Get a staff role"],
        non_negotiables=["No on-call"],
    )
    goals.save(storage)
    loaded = Goals.load_or_empty(storage)
    assert loaded.short_term == ["Get a staff role"]
    assert loaded.non_negotiables == ["No on-call"]


def test_outreach_message_referral_state_rejects_unknown_value(tmp_workspace):
    storage = tmp_workspace.storage
    storage.atomic_write(
        "outreach/bad.json",
        b'{"id":"bad","job_id":"j1","person_id":"p1","draft_text":"hi",'
        b'"referral_state":"banana","created_at":"2026-09-22T00:00:00+00:00"}',
    )
    with pytest.raises(ValidationError):
        OutreachMessage.load(storage, "bad")


def test_connection_request_round_trips(tmp_workspace):
    storage = tmp_workspace.storage
    request = ConnectionRequest(
        id=make_message_id("acme-sre-abc1", "acme-corp-jane-doe"),
        job_id="acme-sre-abc1", person_id="acme-corp-jane-doe",
        linkedin_url="https://www.linkedin.com/in/jane-doe/",
        note_text="Hi Jane — I'm looking at the Senior SRE role.",
    )
    request.save(storage)
    loaded = ConnectionRequest.load(storage, request.id)
    assert loaded.job_id == "acme-sre-abc1"
    assert loaded.person_id == "acme-corp-jane-doe"
    assert loaded.linkedin_url == "https://www.linkedin.com/in/jane-doe/"
    assert loaded.note_text == "Hi Jane — I'm looking at the Senior SRE role."
    # Mirrors OutreachMessage: a freshly persisted record is not sent, and
    # sent_at stays None until an actual send sets it. Phase 13b's
    # duplicate-request refusal keys off these, so their defaults are
    # load-bearing rather than cosmetic.
    assert loaded.send_state == "drafted"
    assert loaded.sent_at is None


def test_connection_request_is_stored_under_connections(tmp_workspace):
    storage = tmp_workspace.storage
    request = ConnectionRequest(
        id="acme-sre-abc1__acme-corp-jane-doe",
        job_id="acme-sre-abc1", person_id="acme-corp-jane-doe",
        linkedin_url="https://www.linkedin.com/in/jane-doe/", note_text="Hi Jane",
    )
    request.save(storage)
    assert storage.exists("connections/acme-sre-abc1__acme-corp-jane-doe.json")


def test_connection_request_load_missing_raises(tmp_workspace):
    with pytest.raises(FileNotFoundError):
        ConnectionRequest.load(tmp_workspace.storage, "nonexistent")


def test_connection_request_load_malformed_raises_value_error(tmp_workspace):
    # Callers guard loads with `except (FileNotFoundError, ValueError)`, which
    # only covers a corrupt or hand-edited record because pydantic's
    # ValidationError subclasses ValueError. Pinned here so a future change to
    # the load path cannot quietly turn a bad file into an unhandled crash.
    storage = tmp_workspace.storage
    storage.atomic_write("connections/bad.json", b'{"id":"bad","job_id":"j1"}')
    with pytest.raises(ValueError):
        ConnectionRequest.load(storage, "bad")


def test_connection_request_id_from_unsafe_person_id_stays_under_connections(tmp_workspace):
    # person_id reaches here from CLI arguments or an agent call, so an id
    # built through make_message_id must never become a traversing path
    # segment. The slug helper already handles this; this pins that the new
    # record benefits from it rather than growing its own id scheme.
    storage = tmp_workspace.storage
    request_id = make_message_id("acme-sre-abc1", "../../etc/passwd")
    assert "/" not in request_id
    request = ConnectionRequest(
        id=request_id, job_id="acme-sre-abc1", person_id="../../etc/passwd",
        linkedin_url="https://www.linkedin.com/in/jane-doe/", note_text="Hi Jane",
    )
    request.save(storage)

    root = Path(tmp_workspace.storage.resolve("."))
    written = [
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and request_id in p.name
    ]
    assert written == ["connections/" + request_id + ".json"]
    assert ConnectionRequest.load(storage, request_id).person_id == "../../etc/passwd"
