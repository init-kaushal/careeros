import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from careeros.cli.outreach_cmd import FOLLOW_UP_CMD_ACTION_LABEL, outreach_app
from careeros.core.models import (
    CadencePolicy, Company, Job, OutreachMessage, Person, PolicyConfig, Profile,
)
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
    closed_reason=None, sent=True, company_name="Acme Corp",
):
    job_id = "acme-sre-" + str(idx)
    company_id = "acme-corp-" + str(idx)
    person_id = company_id + "-jane-doe"
    now = datetime.now(timezone.utc).isoformat()
    Job(
        id=job_id, source="browse", url="https://example.com/job/" + job_id,
        company=company_name, title="Senior SRE", stage="saved",
        created_at=now, updated_at=now,
    ).save(storage)
    Company(id=company_id, name=company_name, researched_at=now).save(storage)
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


def _log_events(storage):
    return [json.loads(line) for line in _today_log(storage).splitlines() if line]


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
        ), patch("careeros.operations.follow_up.send_email") as mock_send_email:
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
        ), patch("careeros.operations.follow_up.send_email"):
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
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
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

        calls = []

        def _flaky_propose(runtime, job_id, person_id, **kwargs):
            calls.append(job_id)
            if len(calls) == 1:
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
        ), patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        log = _today_log(storage)
        assert "DraftFailed" in log
        # The point of the test, and what exit-code-plus-a-log-string alone
        # did not check: the run reached the *second* relationship and
        # proposed it. Turning the error handler's `continue` into a `break`
        # left that assertion pair green, so it was verifying nothing.
        assert sorted(calls) == sorted([job_id_1, job_id_2])
        assert "Proposed: 1" in result.output

    def test_dry_run_lists_due_and_proposes_nothing(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        job_id, person_id, message_id = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
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
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
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


class TestUnusableTouchTimestamp:
    """One bad timestamp must cost one relationship, not the whole run.

    last_touched_at is an unvalidated `str | None` on the model. careeros
    never writes a bad one itself, so every trigger here is external — a
    hand edit, or an agent writing through the documented integration
    contract. Before this, the parse sat outside the enumeration loop's try
    and a single bad value exited 1 with empty output, proposing nothing for
    any healthy relationship.
    """

    @pytest.mark.parametrize("bad_value", [
        # Unparseable: datetime.fromisoformat raises ValueError.
        "not-a-date",
        # Perfectly legal ISO 8601, but offset-naive, so subtracting it from
        # an aware now raises TypeError instead.
        "2026-01-01T10:00:00",
        # Date-only does the same thing, and is the likelier hand edit.
        "2026-09-01",
    ])
    def test_the_bad_record_is_skipped_and_every_healthy_one_is_proposed(
        self, tmp_path, bad_value,
    ):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, _, bad_id = _seed_relationship(
            storage, 0, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )
        OutreachMessage.load(storage, bad_id).model_copy(
            update={"last_touched_at": bad_value},
        ).save(storage)
        for idx in (1, 2, 3):
            _seed_relationship(storage, idx, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_generate.call_count == 3

        from careeros.operations.approvals import list_pending
        pending = list_pending(storage)
        assert len(pending) == 3
        assert bad_id not in {approval.entity_id for approval in pending}

        # Visible, not silent: only the operator can fix the value.
        assert "Skipping" in result.output
        assert bad_id in result.output

    def test_propose_follow_up_refuses_it_structurally_too(self, tmp_path):
        """The operation is protected independently of the command's filter.

        An agent calling propose_follow_up directly gets a refusal it can
        branch on, not a raw TypeError out of the operations layer.
        """
        from careeros.operations.errors import MalformedTouchTimestamp
        from careeros.operations.follow_up import propose_follow_up
        from careeros.runtime.factory import open_local_runtime

        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        job_id, person_id, message_id = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )
        OutreachMessage.load(storage, message_id).model_copy(
            update={"last_touched_at": "2026-01-01T10:00:00"},
        ).save(storage)
        runtime = open_local_runtime(storage)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
        ) as mock_generate:
            with pytest.raises(MalformedTouchTimestamp) as exc:
                propose_follow_up(
                    runtime, job_id, person_id, action_label="follow_up",
                )

        assert exc.value.message_id == message_id
        assert exc.value.value == "2026-01-01T10:00:00"
        # A refusal, so nothing was paid for and nothing was written.
        mock_generate.assert_not_called()
        assert [p for p in storage.list("approvals/") if p.endswith(".json")] == []


class TestPaidAttemptBudget:
    """max_follow_ups_per_run has to bound cost, not successes.

    DraftFailed is raised *after* the LLM call is paid for, because
    generate_follow_up_message swallows every litellm exception into "". So
    an outage or an expired key used to turn every due relationship into a
    paid, discarded call with the cap stopping nothing.
    """

    def test_a_failed_draft_spends_a_slot(self, tmp_path):
        ws_path, storage = _setup_workspace(
            tmp_path, cadence=_default_cadence(max_follow_ups_per_run=1),
        )
        for idx in (1, 2, 3, 4, 5):
            _seed_relationship(storage, idx, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value="",
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        # Measured at 5 before this fix: one paid call per due relationship,
        # every run, forever.
        assert mock_generate.call_count == 1

        from careeros.operations.approvals import list_pending
        assert list_pending(storage) == []
        assert "Deferred by cap: 4" in result.output

    def test_a_policy_block_does_not_spend_a_slot(self, tmp_path):
        """PolicyBlocked is refused before drafting, so it costs nothing.

        Also the only test of the `except PolicyBlocked` handler's
        `continue`: turning it into a `break` leaves the second relationship
        unproposed and fails here. Nothing else catches it.
        """
        ws_path, storage = _setup_workspace(
            tmp_path, cadence=_default_cadence(max_follow_ups_per_run=1),
        )
        PolicyConfig(blocked_companies=["Blocked Inc"]).save(storage)
        # Sorted enumeration puts relationship 1 — the blocked one — first.
        _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
            company_name="Blocked Inc",
        )
        _, _, message_id_2 = _seed_relationship(
            storage, 2, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_generate.call_count == 1

        from careeros.operations.approvals import list_pending
        pending = list_pending(storage)
        assert [approval.entity_id for approval in pending] == [message_id_2]
        assert "Blocked: 1" in result.output
        assert "Proposed: 1" in result.output


class TestAbortOnConsecutiveDraftFailures:
    def test_three_in_a_row_stops_the_whole_run(self, tmp_path):
        """Three consecutive failures mean the provider is down.

        Same reasoning as discover-and-apply's profile_busy
        BrowserUnavailable: continuing buys nothing but identical paid
        failures, so the run stops rather than working through 200 due
        relationships at the provider's expense.
        """
        ws_path, storage = _setup_workspace(
            tmp_path, cadence=_default_cadence(max_follow_ups_per_run=10),
        )
        for idx in (1, 2, 3, 4, 5, 6):
            _seed_relationship(storage, idx, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value="",
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert mock_generate.call_count == 3
        # Nonzero, so a cron run that achieved nothing is distinguishable
        # from one with nothing to do.
        assert result.exit_code == 1
        assert "Aborting" in result.output
        assert "Not examined (run aborted): 3" in result.output
        assert any(
            event["event_type"] == "follow_up_run_aborted"
            and event["status"] == "failed"
            for event in _log_events(storage)
        )

    def test_a_success_between_failures_resets_the_streak(self, tmp_path):
        """Two unlucky relationships either side of a success is not an outage."""
        ws_path, storage = _setup_workspace(
            tmp_path, cadence=_default_cadence(max_follow_ups_per_run=10),
        )
        for idx in (1, 2, 3, 4):
            _seed_relationship(storage, idx, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        drafts = ["", "", FOLLOW_UP_DRAFT, ""]

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            side_effect=drafts,
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_generate.call_count == 4
        assert "Proposed: 1" in result.output
        assert "Errors: 3" in result.output


class TestRunSummaryIsTrue:
    """No number or sentence in the summary may be false.

    It is the operator's entire view of the cadence in a cron mail.
    """

    def test_relationships_awaiting_review_are_counted_as_due(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        for idx in (1, 2, 3):
            _seed_relationship(storage, idx, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
            first = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])
            assert first.exit_code == 0
            assert "Due: 3" in first.output
            assert "Proposed: 3" in first.output

            second = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert second.exit_code == 0
        # Three genuinely past-due relationships printed "Due: 0" before this
        # fix, because the pending-skip filter was applied before the count.
        assert "Due: 3" in second.output
        assert "0 actionable" in second.output
        assert "3 awaiting review" in second.output
        assert "Proposed: 0" in second.output
        # And still no re-drafting.
        assert mock_generate.call_count == 3

    def test_dry_run_does_not_claim_nothing_is_due(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        for idx in (1, 2, 3):
            _seed_relationship(storage, idx, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ), patch("careeros.operations.follow_up.send_email"):
            runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])
            result = runner.invoke(
                outreach_app, ["follow-up", "--workspace", ws_path, "--dry-run"],
            )

        assert result.exit_code == 0
        assert "No relationships are due for a follow-up." not in result.output
        assert "awaiting review" in result.output

    def test_dry_run_still_says_so_when_nothing_is_due(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _seed_relationship(storage, 1, days_since_touch=1)

        result = runner.invoke(
            outreach_app, ["follow-up", "--workspace", ws_path, "--dry-run"],
        )

        assert result.exit_code == 0
        assert "No relationships are due for a follow-up." in result.output

    def test_the_cap_accounts_for_what_it_held_back(self, tmp_path):
        ws_path, storage = _setup_workspace(
            tmp_path, cadence=_default_cadence(max_follow_ups_per_run=2),
        )
        for idx in (1, 2, 3, 4, 5, 6, 7):
            _seed_relationship(storage, idx, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ), patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        # "Due: 7, Proposed: 2" used to account for none of the other five.
        assert "Due: 7" in result.output
        assert "Proposed: 2" in result.output
        assert "Deferred by cap: 5" in result.output


class TestEnumerationIsDeduped:
    def test_a_nested_copy_is_not_enumerated_twice(self, tmp_path):
        """storage.list is a recursive rglob; the id slice assumes a flat layout.

        Measured before the fix: two paid LLM calls for one relationship, an
        immediately-superseded approval, and two of the cap consumed. The
        pending-skip set is computed once before the loop, so it cannot help
        within a single run.
        """
        ws_path, storage = _setup_workspace(
            tmp_path, cadence=_default_cadence(max_follow_ups_per_run=2),
        )
        _, _, message_id = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )
        # A backup or archive copy someone dropped under outreach/.
        storage.atomic_write(
            "outreach/archive/" + message_id + ".json",
            storage.read("outreach/" + message_id + ".json"),
        )

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_generate.call_count == 1
        assert "Due: 1" in result.output

        from careeros.operations.approvals import list_pending
        assert len(list_pending(storage)) == 1


class TestAuditTrailProperties:
    def test_every_event_is_stamped_as_the_automation_runtime(self, tmp_path):
        """This command opens an automation runtime, not a local one.

        The distinction is the whole point of the agent_runtime stamp: a
        scheduled proposer's activity must be attributable to automation
        rather than to a person sitting at a terminal.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _seed_relationship(storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ), patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        events = _log_events(storage)
        assert events
        assert {event["agent_runtime"] for event in events} == {"automation"}

    def test_every_event_carries_this_commands_action_label(self, tmp_path):
        """FOLLOW_UP_CMD_ACTION_LABEL separates this trail from `outreach send`.

        Its value is load-bearing, not cosmetic: it is how the log tells the
        scheduled proposer's work apart from an interactive send.
        """
        assert FOLLOW_UP_CMD_ACTION_LABEL == "outreach-follow-up"

        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _seed_relationship(storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ), patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        events = _log_events(storage)
        assert events
        assert {event["action"] for event in events} == {FOLLOW_UP_CMD_ACTION_LABEL}

    def test_a_propose_error_records_the_detail_not_just_the_type(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _seed_relationship(storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value="",
        ), patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        errors = [
            event for event in _log_events(storage)
            if event["event_type"] == "follow_up_propose_error"
        ]
        assert len(errors) == 1
        assert "DraftFailed" in errors[0]["summary"]
        # str(exc) holds the actual detail; the type name alone discards it.
        assert "Follow-up message generation failed." in errors[0]["summary"]


class TestInvalidCadencePolicy:
    def test_an_invalid_policy_gets_a_message_not_a_traceback(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=None)
        # days_between_touches has ge=1, so 0 fails validation. A *missing*
        # policy already got a friendly message; an invalid one raised a raw
        # pydantic ValidationError at the same operator.
        storage.atomic_write(
            "config/cadence_policy.json",
            b'{"days_between_touches": 0, "max_touches": 3, "max_follow_ups_per_run": 5}',
        )

        result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "cadence_policy.json" in result.output
        assert "not a valid cadence policy" in result.output
