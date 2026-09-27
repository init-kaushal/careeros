from careeros.llm import DEFAULT_MODEL
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from careeros.core.models import Company, Goals, Job, Person, Profile


def _make_job():
    now = datetime.now(timezone.utc).isoformat()
    return Job(
        id="acme-sre-abc1", source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now,
    )


def _make_company():
    return Company(id="acme-corp", name="Acme Corp", industry="Software", researched_at="2026-09-19T00:00:00Z")


def _make_person(role_category="ic"):
    return Person(
        id="acme-corp-jane-doe", company_id="acme-corp", name="Jane Doe",
        role_category=role_category, title="Senior Engineer", researched_at="2026-09-19T00:00:00Z",
    )


def _make_profile():
    return Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE")


class TestGenerateFollowUpMessage:
    def test_returns_generated_text_stripped(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "  \nJust circling back on this.\n  "
        with patch("careeros.llm.litellm.completion", return_value=mock_resp):
            result = generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, I noticed we both...", touch_number=2,
            )
        assert result == "Just circling back on this."

    def test_returns_empty_string_on_failure(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        with patch("careeros.llm.litellm.completion", side_effect=Exception("boom")):
            result = generate_follow_up_message(
                _make_person("em"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, I noticed we both...", touch_number=1,
            )
        assert result == ""

    def test_returns_empty_string_on_empty_content(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = None
        with patch("careeros.llm.litellm.completion", return_value=mock_resp):
            result = generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, I noticed we both...", touch_number=1,
            )
        assert result == ""

    def test_uses_careeros_model_env_var(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        with patch("careeros.llm.litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, I noticed we both...", touch_number=1,
            )
        assert mock_llm.call_args.kwargs["model"] == "gpt-4o"

    def test_falls_back_to_default_model_without_env(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        env_without_careeros_model = {k: v for k, v in os.environ.items() if k != "CAREEROS_MODEL"}
        with patch("careeros.llm.litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, env_without_careeros_model, clear=True):
            generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, I noticed we both...", touch_number=1,
            )
        assert mock_llm.call_args.kwargs["model"] == DEFAULT_MODEL

    def test_model_param_overrides_env(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        with patch("careeros.llm.litellm.completion", return_value=mock_resp) as mock_llm, \
             patch.dict(os.environ, {"CAREEROS_MODEL": "gpt-4o"}):
            generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, I noticed we both...", touch_number=1,
                model="claude-haiku-4-5-20251001",
            )
        assert mock_llm.call_args.kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_prior_text_is_in_untrusted_block_not_system_message(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        prior = "Hi Jane, I noticed we both work on distributed systems..."
        with patch("careeros.llm.litellm.completion", return_value=mock_resp) as mock_llm:
            generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text=prior, touch_number=2,
            )
        messages = mock_llm.call_args.kwargs["messages"]
        assert len(messages) == 2
        system_message, user_message = messages[0], messages[1]
        assert system_message["role"] == "system"
        assert user_message["role"] == "user"
        assert "<untrusted_content>" in user_message["content"]
        assert prior in user_message["content"]
        assert prior not in system_message["content"]

    def test_touch_number_reaches_prompt(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        captured_prompts = []

        def _capture(*args, **kwargs):
            captured_prompts.append(kwargs["messages"][1]["content"])
            return mock_resp

        with patch("careeros.llm.litellm.completion", side_effect=_capture):
            generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, following up on my note.", touch_number=1,
            )
            generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, following up on my note.", touch_number=3,
            )

        assert captured_prompts[0] != captured_prompts[1]
        assert "1" in captured_prompts[0]
        assert "3" in captured_prompts[1]

    def test_sends_system_and_wrapped_user_message(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        with patch("careeros.llm.litellm.completion", return_value=mock_resp) as mock_llm:
            generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, following up.", touch_number=1,
            )
        messages = mock_llm.call_args.kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "<untrusted_content>" in messages[1]["content"]
        assert "Jane Doe" in messages[1]["content"]
        assert "Acme Corp" in messages[1]["content"]

    def test_trusted_profile_fields_stay_in_system_message(self):
        from careeros.skills.follow_up_draft import generate_follow_up_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        with patch("careeros.llm.litellm.completion", return_value=mock_resp) as mock_llm:
            generate_follow_up_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals(),
                prior_text="Hi Jane, following up.", touch_number=1,
            )
        messages = mock_llm.call_args.kwargs["messages"]
        assert "Senior SRE" in messages[0]["content"]
        assert "Jane Doe" not in messages[0]["content"]
