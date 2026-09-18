import os
import pytest
from unittest.mock import MagicMock, patch
from careeros.core.models import Goals, Profile, Skill, Skills


def _make_profile():
    return Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE", summary="SRE with 6 years")


def _make_skills():
    return Skills(skills=[Skill(name="Kubernetes"), Skill(name="Go"), Skill(name="Terraform")])


def _make_goals():
    return Goals(short_term=["platform engineering role", "remote work"], long_term=["staff engineer"])


class TestGenerateCoverLetter:
    def test_returns_text_on_success(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Dear Hiring Manager,\n\nI am excited..."
        with patch("litellm.completion", return_value=mock_resp):
            result = generate_cover_letter("SRE job description", _make_profile(), _make_skills(), _make_goals())
        assert result == "Dear Hiring Manager,\n\nI am excited..."

    def test_returns_empty_string_on_llm_error(self):
        from careeros.skills.cover_letter import generate_cover_letter
        with patch("litellm.completion", side_effect=Exception("API error")):
            result = generate_cover_letter("SRE job", _make_profile(), _make_skills(), _make_goals())
        assert result == ""

    def test_returns_empty_string_on_missing_content(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = None
        with patch("litellm.completion", return_value=mock_resp):
            result = generate_cover_letter("SRE job", _make_profile(), _make_skills(), _make_goals())
        assert result == ""

    def test_strips_whitespace_from_response(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "  \nDear Hiring Manager,\n\nText here.\n  "
        with patch("litellm.completion", return_value=mock_resp):
            result = generate_cover_letter("SRE job", _make_profile(), _make_skills(), _make_goals())
        assert result == "Dear Hiring Manager,\n\nText here."

    def test_uses_careeros_model_env_var(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            generate_cover_letter("jd", _make_profile(), _make_skills(), _make_goals())
        assert mock_llm.call_args[1]["model"] == "gpt-4o"

    def test_model_param_overrides_env(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            generate_cover_letter("jd", _make_profile(), _make_skills(), _make_goals(), model="claude-haiku-4-5-20251001")
        assert mock_llm.call_args[1]["model"] == "claude-haiku-4-5-20251001"

    def test_profile_title_in_prompt(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            generate_cover_letter("some jd text", _make_profile(), _make_skills(), _make_goals())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "Senior SRE" in prompt

    def test_jd_capped_at_4000_chars(self):
        from careeros.skills.cover_letter import generate_cover_letter
        long_jd = "x" * 5000
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            generate_cover_letter(long_jd, _make_profile(), _make_skills(), _make_goals())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "x" * 4001 not in prompt

    def test_goals_in_prompt(self):
        from careeros.skills.cover_letter import generate_cover_letter
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Cover letter"
        with patch("litellm.completion", return_value=mock_resp) as mock_llm:
            generate_cover_letter("jd text", _make_profile(), _make_skills(), _make_goals())
        prompt = mock_llm.call_args[1]["messages"][0]["content"]
        assert "platform engineering role" in prompt
