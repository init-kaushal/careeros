import os
import pytest
from unittest.mock import MagicMock, patch
from careeros.core.models import Profile, Skill, Skills, Goals


def _make_profile():
    return Profile(name="Alice", title="Senior SRE", summary="SRE with 6 years experience")


def _make_skills():
    return Skills(skills=[Skill(name="Kubernetes"), Skill(name="Go"), Skill(name="Terraform")])


def _make_goals():
    return Goals(short_term=["platform role", "remote work"], long_term=["staff engineer"])


class TestScoreJob:
    def test_returns_full_dict_on_success(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 85, "reasoning": "Great fit.", "strengths": ["Kubernetes"], "gaps": ["Java"]}'
        with patch("litellm.completion", return_value=mock_resp):
            result = score_job("SRE job posting", _make_profile(), _make_skills())
        assert result["score"] == 85
        assert result["reasoning"] == "Great fit."
        assert "Kubernetes" in result["strengths"]
        assert isinstance(result["gaps"], list)

    def test_returns_fallback_on_llm_error(self):
        from careeros.skills.job_score import score_job
        with patch("litellm.completion", side_effect=Exception("API error")):
            result = score_job("SRE job posting", _make_profile(), _make_skills())
        assert result == {"score": 0, "reasoning": "Could not score.", "strengths": [], "gaps": []}

    def test_returns_fallback_on_bad_json(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "not json at all"
        with patch("litellm.completion", return_value=mock_resp):
            result = score_job("SRE job posting", _make_profile(), _make_skills())
        assert result["score"] == 0

    def test_strips_markdown_fences_from_response(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '```json\n{"score": 70, "reasoning": "ok", "strengths": [], "gaps": []}\n```'
        with patch("litellm.completion", return_value=mock_resp):
            result = score_job("SRE job posting", _make_profile(), _make_skills())
        assert result["score"] == 70

    def test_uses_careeros_model_env_var(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 50, "reasoning": "ok", "strengths": [], "gaps": []}'
        with patch("litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            score_job("text", _make_profile(), _make_skills())
        call_model = mock_llm.call_args[1]["model"]
        assert call_model == "gpt-4o"

    def test_model_param_overrides_env(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 50, "reasoning": "ok", "strengths": [], "gaps": []}'
        with patch("litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            score_job("text", _make_profile(), _make_skills(), model="claude-haiku-4-5-20251001")
        assert mock_llm.call_args[1]["model"] == "claude-haiku-4-5-20251001"

    def test_profile_title_appears_in_prompt(self):
        from careeros.skills.job_score import score_job
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 50, "reasoning": "ok", "strengths": [], "gaps": []}'
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            score_job("some jd text", _make_profile(), _make_skills())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "Senior SRE" in prompt

    def test_jd_text_capped_at_4000_chars(self):
        from careeros.skills.job_score import score_job
        long_jd = "x" * 5000
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"score": 50, "reasoning": "ok", "strengths": [], "gaps": []}'
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            score_job(long_jd, _make_profile(), _make_skills())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "x" * 4001 not in prompt
        assert "x" * 4000 in prompt or prompt.count("x") <= 4000


class TestJobQueryFromProfile:
    def test_includes_profile_title(self):
        from careeros.skills.browse_query import job_query_from_profile
        profile = Profile(name="Alice", title="Senior SRE")
        goals = Goals(short_term=["platform role", "Kubernetes focus"], long_term=["staff engineer"])
        query = job_query_from_profile(profile, goals)
        assert "Senior SRE" in query

    def test_max_80_chars(self):
        from careeros.skills.browse_query import job_query_from_profile
        profile = Profile(name="Alice", title="A" * 50)
        goals = Goals(short_term=["B" * 40], long_term=["C" * 40])
        query = job_query_from_profile(profile, goals)
        assert len(query) <= 80

    def test_empty_profile_does_not_crash(self):
        from careeros.skills.browse_query import job_query_from_profile
        query = job_query_from_profile(Profile(), Goals())
        assert isinstance(query, str)
