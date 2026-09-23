from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from careeros.cli.outreach_cmd import outreach_app
from careeros.core.models import CadencePolicy, Company, Job, OutreachMessage, Person, Profile
from careeros.operations.follow_up import ACTION as FOLLOW_UP_ACTION
from careeros.operations.outreach import make_message_id
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()

PRIOR_DRAFT = "Hi Jane, I saw the Senior SRE role at Acme Corp..."
FOLLOW_UP_DRAFT = "Hi Jane, just bumping this to the top of your inbox."

DAYS_BETWEEN_TOUCHES = 5
MAX_TOUCHES = 3


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _setup_workspace(tmp_path, cadence=None):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    if cadence is not None:
        cadence.save(storage)
    return str(tmp_path), storage


def _default_cadence(max_follow_ups_per_run=5):
    return CadencePolicy(
        days_between_touches=DAYS_BETWEEN_TOUCHES, max_touches=MAX_TOUCHES,
        max_follow_ups_per_run=max_follow_ups_per_run,
    )


def _seed_relationship(
    storage, idx, *, days_since_touch, touch_count=1, referral_state="research",
    closed_reason=None, sent=True,
):
    job_id = "acme-sre-" + str(idx)
    company_id = "acme-corp-" + str(idx)
    person_id = company_id + "-jane-doe"
    now = datetime.now(timezone.utc).isoformat()
    Job(
        id=job_id, source="browse", url="https://example.com/job/" + job_id,
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now,
    ).save(storage)
    Company(id=company_id, name="Acme Corp", researched_at=now).save(storage)
    Person(
        id=person_id, company_id=company_id, name="Jane Doe", role_category="em",
        title="Engineering Manager", email="jane@acme.com", researched_at=now,
    ).save(storage)
    message_id = make_message_id(job_id, person_id)
    touch_at = _iso(days_since_touch) if sent else None
    OutreachMessage(
        id=message_id, job_id=job_id, person_id=person_id, draft_text=PRIOR_DRAFT,
        send_state="sent" if sent else "drafted", referral_state=referral_state,
        created_at=now, sent_at=touch_at, last_touched_at=touch_at,
        touch_count=touch_count, closed_reason=closed_reason,
    ).save(storage)
    return job_id, person_id, message_id


def _today_log(storage):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = "activity/" + date + ".jsonl"
    if not storage.exists(path):
        return ""
    return storage.read(path).decode()


class TestFollowUpCmd:
    def test_missing_cadence_policy_exits_1(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=None)

        result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 1
        assert "cadence" in result.output.lower()

    def test_due_relationship_gets_a_pending_approval_and_never_sends(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        job_id, person_id, message_id = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ), patch("careeros.mailer.send_email") as mock_send_email:
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send_email.assert_not_called()

        from careeros.operations.approvals import list_pending
        pending = list_pending(storage)
        assert len(pending) == 1
        assert pending[0].action == FOLLOW_UP_ACTION
        assert pending[0].entity_id == message_id

        message = OutreachMessage.load(storage, message_id)
        assert message.draft_text == FOLLOW_UP_DRAFT
        # Nothing about sending has happened yet.
        assert message.send_state != "sent" or message.touch_count == 1

    def test_max_follow_ups_per_run_caps_how_many_are_proposed(self, tmp_path):
        ws_path, storage = _setup_workspace(
            tmp_path, cadence=_default_cadence(max_follow_ups_per_run=1),
        )
        _seed_relationship(storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)
        _seed_relationship(storage, 2, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ), patch("careeros.mailer.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        from careeros.operations.approvals import list_pending
        pending = list_pending(storage)
        assert len(pending) == 1

    def test_not_due_relationship_is_skipped_with_no_activity_event(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _seed_relationship(storage, 1, days_since_touch=1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
        ) as mock_generate, patch("careeros.mailer.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        mock_generate.assert_not_called()
        log = _today_log(storage)
        assert "acme-corp-1-jane-doe" not in log
        assert "follow_up" not in log

    def test_operation_error_on_one_relationship_is_logged_and_run_continues(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        job_id_1, person_id_1, message_id_1 = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )
        job_id_2, person_id_2, message_id_2 = _seed_relationship(
            storage, 2, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )

        from careeros.operations.errors import DraftFailed

        call_count = {"n": 0}

        def _flaky_propose(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise DraftFailed("Follow-up message generation failed.")
            from careeros.operations.follow_up import FollowUpProposal
            return FollowUpProposal(
                approval_id="fake-approval", message_id=message_id_2,
                summary="summary", draft_text=FOLLOW_UP_DRAFT,
                recipient_name="Jane Doe", recipient_email="jane@acme.com",
                subject="Re: something", touch_number=2, days_since_last_touch=6,
            )

        with patch(
            "careeros.cli.outreach_cmd.propose_follow_up", side_effect=_flaky_propose,
        ), patch("careeros.mailer.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        log = _today_log(storage)
        assert "DraftFailed" in log

    def test_dry_run_lists_due_and_proposes_nothing(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        job_id, person_id, message_id = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
        ) as mock_generate, patch("careeros.mailer.send_email"):
            result = runner.invoke(
                outreach_app, ["follow-up", "--workspace", ws_path, "--dry-run"],
            )

        assert result.exit_code == 0
        mock_generate.assert_not_called()
        assert message_id in result.output

        from careeros.operations.approvals import list_pending
        assert list_pending(storage) == []

    def test_second_run_skips_relationship_with_a_pending_follow_up_approval(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        job_id, person_id, message_id = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ) as mock_generate, patch("careeros.mailer.send_email"):
            first = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])
            assert first.exit_code == 0
            assert mock_generate.call_count == 1

            second = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])
            assert second.exit_code == 0
            # The defining assertion: the drafting skill must not be called
            # again for a relationship that already has a pending approval —
            # an approval-count assertion alone would pass even if this
            # quietly re-drafted every run.
            assert mock_generate.call_count == 1

        from careeros.operations.approvals import list_pending
        assert len(list_pending(storage)) == 1
