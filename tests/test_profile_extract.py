import pytest
from unittest.mock import patch, MagicMock
from careeros.skills.profile_extract import extract_basic_profile, DEFAULT_LLM_MODEL
from careeros.core.models import Profile, Skills


def _mock_completion(texts: list[str]) -> list[MagicMock]:
    resps = []
    for text in texts:
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = text
        resps.append(resp)
    return resps


def test_returns_profile_and_skills():
    profile_json = '{"name":"Alice Johnson","title":"Senior SRE","years_of_experience":8,"location":"San Francisco, CA","email":"alice@example.com","summary":"SRE with 8 years."}'
    skills_json = '{"skills":[{"name":"Python","level":"expert","source":"resume"},{"name":"Kubernetes","level":"advanced","source":"resume"}]}'

    with patch("litellm.completion", side_effect=_mock_completion([profile_json, skills_json])):
        profile, skills = extract_basic_profile("dummy resume")

    assert profile.name == "Alice Johnson"
    assert profile.years_of_experience == 8
    assert profile.title == "Senior SRE"
    assert len(skills.skills) == 2
    assert skills.skills[0].name == "Python"
    assert skills.skills[0].level == "expert"


def test_handles_null_fields():
    profile_json = '{"name":"Bob","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'

    with patch("litellm.completion", side_effect=_mock_completion([profile_json, skills_json])):
        profile, skills = extract_basic_profile("short bio")

    assert profile.name == "Bob"
    assert profile.title is None
    assert skills.skills == []


def test_makes_exactly_two_api_calls():
    profile_json = '{"name":"Charlie","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'

    with patch("litellm.completion", side_effect=_mock_completion([profile_json, skills_json])) as mock_comp:
        extract_basic_profile("resume text")

    assert mock_comp.call_count == 2


def test_uses_default_model(monkeypatch):
    monkeypatch.delenv("CAREEROS_MODEL", raising=False)
    profile_json = '{"name":"Dana","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'

    with patch("litellm.completion", side_effect=_mock_completion([profile_json, skills_json])) as mock_comp:
        extract_basic_profile("resume text")

    for call in mock_comp.call_args_list:
        assert call.kwargs["model"] == DEFAULT_LLM_MODEL


def test_respects_model_env_var(monkeypatch):
    monkeypatch.setenv("CAREEROS_MODEL", "gpt-4o-mini")
    profile_json = '{"name":"Eve","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'

    with patch("litellm.completion", side_effect=_mock_completion([profile_json, skills_json])) as mock_comp:
        extract_basic_profile("resume text")

    for call in mock_comp.call_args_list:
        assert call.kwargs["model"] == "gpt-4o-mini"


def test_model_param_overrides_env(monkeypatch):
    monkeypatch.setenv("CAREEROS_MODEL", "gpt-4o-mini")
    profile_json = '{"name":"Frank","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'

    with patch("litellm.completion", side_effect=_mock_completion([profile_json, skills_json])) as mock_comp:
        extract_basic_profile("resume text", model="ollama/llama3.2")

    for call in mock_comp.call_args_list:
        assert call.kwargs["model"] == "ollama/llama3.2"


def test_returns_empty_sentinel_on_llm_failure():
    with patch("litellm.completion", side_effect=Exception("API error")):
        profile, skills = extract_basic_profile("dummy resume")
    assert profile == Profile()
    assert skills == Skills()


def test_returns_empty_sentinel_on_malformed_json():
    with patch("litellm.completion", side_effect=_mock_completion(["not json at all"])):
        profile, skills = extract_basic_profile("dummy resume")
    assert profile == Profile()
    assert skills == Skills()


def test_resume_text_truncated_at_4000_chars():
    profile_json = '{"name":"Eve","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    long_resume = "x" * 10000

    with patch("litellm.completion", side_effect=_mock_completion([profile_json, skills_json])) as mock_comp:
        extract_basic_profile(long_resume)

    first_call_messages = mock_comp.call_args_list[0].kwargs["messages"]
    sent_content = first_call_messages[0]["content"]
    assert ("x" * 4000) in sent_content
    assert ("x" * 4001) not in sent_content


def test_resume_text_included_in_prompt():
    profile_json = '{"name":"Eve","title":null,"years_of_experience":null,"location":null,"email":null,"summary":null}'
    skills_json = '{"skills":[]}'
    resume = "Eve Smith\nStaff Engineer with 12 years"

    with patch("litellm.completion", side_effect=_mock_completion([profile_json, skills_json])) as mock_comp:
        extract_basic_profile(resume)

    first_call_messages = mock_comp.call_args_list[0].kwargs["messages"]
    assert resume in first_call_messages[0]["content"]
