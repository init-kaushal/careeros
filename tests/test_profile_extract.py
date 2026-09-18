import pytest
from unittest.mock import MagicMock
from careeros.skills.profile_extract import extract_basic_profile
from careeros.core.models import Profile, Skills


def _make_mock_client(profile_json: str, skills_json: str) -> MagicMock:
    client = MagicMock()
    profile_msg = MagicMock()
    profile_msg.content = [MagicMock(text=profile_json)]
    skills_msg = MagicMock()
    skills_msg.content = [MagicMock(text=skills_json)]
    client.messages.create.side_effect = [profile_msg, skills_msg]
    return client


def test_returns_profile_and_skills():
    profile_json = '{"name":"Alice Johnson","title":"Senior SRE","years_of_experience":8,"location":"San Francisco, CA","email":"alice@example.com","summary":"SRE with 8 years."}'
    skills_json = '{"skills":[{"name":"Python","level":"expert","source":"resume"},{"name":"Kubernetes","level":"advanced","source":"resume"}]}'
    client = _make_mock_client(profile_json, skills_json)

    profile, skills = extract_basic_profile("dummy resume", client=client)

    assert profile.name == "Alice Johnson"
    assert profile.years_of_experience == 8
    assert profile.title == "Senior SRE"
    assert len(skills.skills) == 2
    assert skills.skills[0].name == "Python"
    assert skills.skills[0].level == "expert"


def test_handles_null_fields():
    profile_json = '{"name":"Bob","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    client = _make_mock_client(profile_json, skills_json)

    profile, skills = extract_basic_profile("short bio", client=client)

    assert profile.name == "Bob"
    assert profile.title is None
    assert skills.skills == []


def test_makes_exactly_two_api_calls():
    profile_json = '{"name":"Charlie","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    client = _make_mock_client(profile_json, skills_json)

    extract_basic_profile("resume text", client=client)

    assert client.messages.create.call_count == 2


def test_uses_haiku_model():
    profile_json = '{"name":"Dana","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    client = _make_mock_client(profile_json, skills_json)

    extract_basic_profile("resume text", client=client)

    calls = client.messages.create.call_args_list
    for call in calls:
        assert call.kwargs["model"] == "claude-haiku-4-5-20251001"


def test_resume_text_included_in_prompt():
    profile_json = '{"name":"Eve","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    client = _make_mock_client(profile_json, skills_json)
    resume = "Eve Smith\nStaff Engineer with 12 years"

    extract_basic_profile(resume, client=client)

    first_call_messages = client.messages.create.call_args_list[0].kwargs["messages"]
    assert resume in first_call_messages[0]["content"]
