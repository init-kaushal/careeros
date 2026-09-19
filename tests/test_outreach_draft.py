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


class TestGenerateOutreachMessage:
    def test_returns_generated_text_for_ic(self):
        from careeros.skills.outreach_draft import generate_outreach_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Hi Jane, I noticed we both..."
        with patch("careeros.skills.outreach_draft.litellm.completion", return_value=mock_resp):
            result = generate_outreach_message(
                _make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals()
            )
        assert result == "Hi Jane, I noticed we both..."

    def test_uses_different_instructions_per_role_category(self):
        from careeros.skills.outreach_draft import generate_outreach_message
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "Draft text"
        captured_prompts = []

        def _capture(*args, **kwargs):
            captured_prompts.append(kwargs["messages"][0]["content"])
            return mock_resp

        with patch("careeros.skills.outreach_draft.litellm.completion", side_effect=_capture):
            generate_outreach_message(_make_person("ic"), _make_job(), _make_company(), _make_profile(), Goals())
            generate_outreach_message(_make_person("recruiter"), _make_job(), _make_company(), _make_profile(), Goals())

        assert captured_prompts[0] != captured_prompts[1]

    def test_returns_empty_string_on_failure(self):
        from careeros.skills.outreach_draft import generate_outreach_message
        with patch("careeros.skills.outreach_draft.litellm.completion", side_effect=Exception("boom")):
            result = generate_outreach_message(
                _make_person("em"), _make_job(), _make_company(), _make_profile(), Goals()
            )
        assert result == ""
