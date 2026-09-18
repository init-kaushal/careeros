import pytest
from careeros.core.models import Profile, Skill, Skills, Preferences, Goals


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
