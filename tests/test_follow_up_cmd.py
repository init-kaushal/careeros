import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from careeros.cli.outreach_cmd import (
    FOLLOW_UP_CMD_ACTION_LABEL, REVIEW_CMD_ACTION_LABEL, _open_automation_runtime,
    outreach_app,
)
from careeros.core.models import (
    Approval, CadencePolicy, Company, Job, OutreachMessage, Person, PolicyConfig,
    Profile,
)
from careeros.operations.approval_queue import DEFERRED_REASON
from careeros.operations.approvals import list_pending
from careeros.operations.follow_up import ACTION as FOLLOW_UP_ACTION
from careeros.operations.follow_up import check_follow_up_due
from careeros.operations.outreach import make_message_id
from careeros.runtime.base import ActionProposal
from careeros.runtime.factory import open_automation_runtime, resolve_storage
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


class TestTheCommandWiresItsCollaborators:
    """The two fixes that were correct but unguarded.

    A re-review proved both `now=now` and `approval_callback=queue_only`
    could be deleted from this command with the whole suite still green.
    The mechanisms on either side were tested; the command's use of them was
    not, which is the failure mode this branch keeps reproducing — a real
    fix with no test that fails when it regresses.
    """

    def test_the_instant_the_filter_used_is_the_instant_propose_gets(self, tmp_path):
        """One `now` for the whole run, threaded all the way down.

        Without it the filter and each propose_follow_up read the clock
        independently. Skewing only the operations-layer clock by two days
        was enough to turn a genuinely due relationship into `Errors: 1`
        plus a spurious follow_up_propose_error in the audit log, while the
        summary still counted it as due.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _seed_relationship(storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        filter_instants = []
        real_check = check_follow_up_due

        def recording_check(message, policy, now):
            filter_instants.append(now)
            return real_check(message, policy, now)

        with patch(
            "careeros.cli.outreach_cmd.check_follow_up_due", side_effect=recording_check,
        ), patch("careeros.cli.outreach_cmd.propose_follow_up") as mock_propose:
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        # Fails loudly rather than silently if the kwarg is dropped entirely.
        propose_instants = [call.kwargs["now"] for call in mock_propose.call_args_list]
        assert propose_instants, "propose_follow_up was never called"
        assert filter_instants, "the due-ness filter was never consulted"
        assert set(propose_instants) == set(filter_instants) == {filter_instants[0]}

    def test_this_command_denies_by_default_rather_than_auto_approving(self, tmp_path):
        """AutomationRuntime's class default is auto-approve; this route's is not.

        That default is right for discover-and-apply, where a wrong decision
        only costs an application. This command proposes and stops, so any
        request_approval it ever reaches must refuse.
        """
        ws_path, _ = _setup_workspace(tmp_path, cadence=_default_cadence())

        runtime = _open_automation_runtime(ws_path)
        decision = runtime.request_approval(
            ActionProposal(action=FOLLOW_UP_ACTION, summary="would this be approved?")
        )

        assert decision.approved is False
        assert decision.reason == DEFERRED_REASON
        # The class-wide default must be untouched for every other caller.
        assert open_automation_runtime(
            resolve_storage(ws_path)
        ).request_approval(
            ActionProposal(action="apply", summary="discover-and-apply's route")
        ).approved is True


class TestABadRecordIsNeverDroppedSilently:
    @pytest.mark.parametrize(
        "payload", [b"{not json", b'{"id": "wrong-schema"}', b"", b"null"],
        ids=["unparseable", "wrong-schema", "empty", "null"],
    )
    def test_a_record_that_cannot_load_is_reported(self, tmp_path, payload):
        """The silent drop was the worse of the two defects.

        A malformed *timestamp* already warned, but a record that would not
        load at all was skipped with no output whatsoever — so a
        relationship could quietly stop being followed up forever.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _seed_relationship(storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)
        storage.atomic_write("outreach/broken.json", payload)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ), patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0
        assert "outreach/broken.json" in result.output
        # The healthy relationship is still proposed: one bad record costs
        # only itself.
        assert "Proposed: 1" in result.output

class TestDryRunPreviewsTheRealRun:
    def test_dry_run_previews_the_cap_it_will_hit(self, tmp_path):
        """A preview that omits the cap overstates the next run."""
        ws_path, storage = _setup_workspace(
            tmp_path, cadence=_default_cadence(max_follow_ups_per_run=2),
        )
        for idx in (1, 2, 3, 4, 5):
            _seed_relationship(storage, idx, days_since_touch=DAYS_BETWEEN_TOUCHES + 1)

        result = runner.invoke(
            outreach_app, ["follow-up", "--workspace", ws_path, "--dry-run"],
        )

        assert result.exit_code == 0
        assert "2 would be drafted this run" in result.output
        assert "3 held back by max_follow_ups_per_run" in result.output


REGENERATED_DRAFT = "Hi Jane, one more thought on that SRE role."


def _queue_a_follow_up(
    storage, ws_path, idx, *, draft=FOLLOW_UP_DRAFT,
    days_since_touch=DAYS_BETWEEN_TOUCHES + 1, touch_count=1,
):
    """Seed a relationship and let the scheduled proposer queue its approval.

    Goes through the real `outreach follow-up` command rather than
    hand-writing an Approval, so every review test drains a queue that was
    filled the way production fills it — including the payload keys, which
    are the contract between the two commands.

    Calling it repeatedly queues exactly the new relationship each time: the
    proposer skips anything that already has a pending follow-up approval,
    which is what lets each queued item carry its own distinguishable draft.
    """
    ids = _seed_relationship(
        storage, idx, days_since_touch=days_since_touch, touch_count=touch_count,
    )
    with patch(
        "careeros.operations.follow_up.generate_follow_up_message", return_value=draft,
    ), patch("careeros.operations.follow_up.send_email") as mock_send_email:
        result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])
    assert result.exit_code == 0
    mock_send_email.assert_not_called()
    return ids


def _pending_follow_ups(storage):
    return [a for a in list_pending(storage) if a.action == FOLLOW_UP_ACTION]


def _approval_states(storage):
    states = {}
    for path in storage.list("approvals/"):
        if not path.endswith(".json"):
            continue
        approval = Approval.load(storage, path[len("approvals/"):-len(".json")])
        states[approval.id] = approval.state
    return states


class TestReviewCmd:
    def test_an_empty_queue_says_so_and_exits_0(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())

        with patch("careeros.cli.outreach_cmd.Prompt.ask") as mock_ask, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert "No follow-ups" in result.output
        # Nothing was asked and nothing was sent: an empty queue is not a
        # prompt the user has to dismiss.
        mock_ask.assert_not_called()
        mock_send_email.assert_not_called()

    def test_accepting_sends_the_draft_that_was_shown(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, _, message_id = _queue_a_follow_up(storage, ws_path, 1)
        approval_id = _pending_follow_ups(storage)[0].id

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        # The bytes on screen are the bytes that transmitted.
        assert "bumping this to the top" in result.output
        mock_send_email.assert_called_once()
        to_address, subject, body = mock_send_email.call_args.args
        assert to_address == "jane@acme.com"
        assert subject.startswith("Re: ")
        assert body == FOLLOW_UP_DRAFT

        assert Approval.load(storage, approval_id).state == "executed"
        message = OutreachMessage.load(storage, message_id)
        assert message.send_state == "sent"
        assert message.touch_count == 2
        assert "1 sent" in result.output

    def test_declining_marks_it_declined_and_advances_last_touched_at(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, _, message_id = _queue_a_follow_up(storage, ws_path, 1)
        approval_id = _pending_follow_ups(storage)[0].id
        before = OutreachMessage.load(storage, message_id).last_touched_at

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="d"), patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send_email.assert_not_called()
        assert Approval.load(storage, approval_id).state == "declined"
        message = OutreachMessage.load(storage, message_id)
        assert message.send_state == "declined"
        # Deferred by one cadence period, not counted as a touch.
        assert message.last_touched_at is not None and message.last_touched_at > before
        assert message.touch_count == 1
        assert "1 declined" in result.output

    def test_skipping_leaves_it_pending_for_a_later_run(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, _, message_id = _queue_a_follow_up(storage, ws_path, 1)
        approval_id = _pending_follow_ups(storage)[0].id
        before = OutreachMessage.load(storage, message_id).last_touched_at

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="s"), patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send_email.assert_not_called()
        # Still pending, and nothing about the relationship moved — so the
        # very next review offers the same draft again.
        assert Approval.load(storage, approval_id).state == "pending"
        assert [a.id for a in _pending_follow_ups(storage)] == [approval_id]
        message = OutreachMessage.load(storage, message_id)
        assert message.last_touched_at == before
        assert message.touch_count == 1
        assert "1 skipped" in result.output

    def test_regenerating_then_accepting_sends_the_new_draft(self, tmp_path):
        """The approval under review changes identity on a regenerate.

        propose_follow_up goes through open_approval, which supersedes the
        approval it replaces — so resolving the id the loop started with
        would raise ApprovalNotGranted. The loop has to switch to the new
        proposal's approval_id and its text.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, _, message_id = _queue_a_follow_up(storage, ws_path, 1)
        original_id = _pending_follow_ups(storage)[0].id

        with patch("careeros.cli.outreach_cmd.Prompt.ask", side_effect=["r", "a"]), patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=REGENERATED_DRAFT,
        ) as mock_generate, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_generate.call_count == 1
        mock_send_email.assert_called_once()
        _, _, body = mock_send_email.call_args.args
        # The newly drafted text is what sent, and what was shown.
        assert body == REGENERATED_DRAFT
        assert "one more thought" in result.output
        assert OutreachMessage.load(storage, message_id).draft_text == REGENERATED_DRAFT

        states = _approval_states(storage)
        assert states[original_id] == "superseded"
        executed = [aid for aid, state in states.items() if state == "executed"]
        assert len(executed) == 1 and executed[0] != original_id

    def test_only_follow_up_approvals_are_touched(self, tmp_path):
        """list_pending returns every pending approval regardless of action.

        Filtering is this command's job, so an unrelated pending outreach or
        apply approval must come out of a review run untouched and
        undisplayed.
        """
        from careeros.operations.apply import ACTION as APPLY_ACTION
        from careeros.operations.approvals import open_approval
        from careeros.operations.outreach import ACTION as OUTREACH_ACTION
        from careeros.runtime.factory import open_local_runtime

        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        job_id, person_id, message_id = _queue_a_follow_up(storage, ws_path, 1)
        follow_up_id = _pending_follow_ups(storage)[0].id

        runtime = open_local_runtime(storage)
        outreach_approval = open_approval(
            runtime, OUTREACH_ACTION, "Send the first outreach email?",
            {"message_id": message_id, "person_id": person_id, "draft_sha256": "x",
             "subject": "Unrelated outreach"},
            entity_type="outreach_message", entity_id=message_id,
            action_label="outreach",
        )
        apply_approval = open_approval(
            runtime, APPLY_ACTION, "Apply to this job?",
            {"job_id": job_id}, entity_type="job", entity_id=job_id,
            action_label="apply",
        )

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a") as mock_ask, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        # One item reviewed, one email sent — not three.
        assert mock_ask.call_count == 1
        mock_send_email.assert_called_once()
        states = _approval_states(storage)
        assert states[follow_up_id] == "executed"
        assert states[outreach_approval.id] == "pending"
        assert states[apply_approval.id] == "pending"
        assert "Unrelated outreach" not in result.output
        assert "Apply to this job?" not in result.output


class TestReviewSurvivesABadItem:
    """One failing item must cost one item, not the rest of the queue."""

    def test_a_missing_recipient_does_not_abandon_the_queue(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, person_id_1, _ = _queue_a_follow_up(storage, ws_path, 1)
        _queue_a_follow_up(storage, ws_path, 2, draft=REGENERATED_DRAFT)
        queue = _pending_follow_ups(storage)
        assert len(queue) == 2
        first_id, second_id = queue[0].id, queue[1].id
        Person.load(storage, person_id_1).model_copy(
            update={"email": None},
        ).save(storage)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a") as mock_ask, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        # A failed item makes the run exit nonzero, but only after the rest
        # of the queue has been offered.
        assert result.exit_code == 1
        assert "No email on file" in result.output
        assert mock_ask.call_count == 2
        # The consequence, not just the exit code: the *second* item really
        # was reviewed and sent.
        mock_send_email.assert_called_once()
        _, _, body = mock_send_email.call_args.args
        assert body == REGENERATED_DRAFT
        states = _approval_states(storage)
        # Nothing was attempted for the first, so it stays approved and
        # retryable rather than being consumed.
        assert states[first_id] == "approved"
        assert states[second_id] == "executed"
        assert "1 failed" in result.output

    def test_a_malformed_payload_does_not_abandon_the_queue(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)
        _queue_a_follow_up(storage, ws_path, 2, draft=REGENERATED_DRAFT)
        queue = _pending_follow_ups(storage)
        first_id, second_id = queue[0].id, queue[1].id
        Approval.load(storage, first_id).model_copy(update={"payload": {}}).save(storage)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a") as mock_ask, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 1
        assert "missing payload key" in result.output
        # The broken item was never even offered — there is nothing to show.
        assert mock_ask.call_count == 1
        mock_send_email.assert_called_once()
        _, _, body = mock_send_email.call_args.args
        assert body == REGENERATED_DRAFT
        states = _approval_states(storage)
        assert states[first_id] == "pending"
        assert states[second_id] == "executed"

    def test_an_out_of_band_edit_to_the_draft_fails_only_that_item(self, tmp_path):
        """ArtifactChanged: the stored draft moved after the approval was written."""
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, _, message_id_1 = _queue_a_follow_up(storage, ws_path, 1)
        _queue_a_follow_up(storage, ws_path, 2, draft=REGENERATED_DRAFT)
        queue = _pending_follow_ups(storage)
        first_id, second_id = queue[0].id, queue[1].id
        OutreachMessage.load(storage, message_id_1).model_copy(
            update={"draft_text": "Edited by hand after the approval was written."},
        ).save(storage)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 1
        # Fragment kept short because rich wraps the message at the console
        # width, so any longer phrase can straddle a line break.
        assert "has changed" in result.output
        mock_send_email.assert_called_once()
        _, _, body = mock_send_email.call_args.args
        assert body == REGENERATED_DRAFT
        states = _approval_states(storage)
        assert states[first_id] == "approved"
        assert states[second_id] == "executed"


class TestReviewToleratesAMissingPersonRecord:
    def test_the_draft_is_still_shown_without_people_json(self, tmp_path):
        """decline_follow_up already falls back to the raw person id here.

        Bookkeeping must not fail on a missing person record, and neither
        must *showing* a draft: the reviewer can still read what would be
        sent and decide.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, person_id, _ = _queue_a_follow_up(storage, ws_path, 1)
        approval_id = _pending_follow_ups(storage)[0].id
        (tmp_path / "people" / (person_id + ".json")).unlink()

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="s") as mock_ask, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_ask.call_count == 1
        assert "bumping this to the top" in result.output
        # Titled with the raw person id, since there is no name to use.
        assert person_id in result.output
        mock_send_email.assert_not_called()
        assert Approval.load(storage, approval_id).state == "pending"


class TestReviewRegenerateCanBeRefused:
    """A re-propose re-runs the full due-ness check, which can now refuse.

    The cron run and the review are separate processes, so the cadence
    policy can have been edited in between. A refusal must leave the item
    pending — the draft the user was offered is still valid and still
    theirs to accept.
    """

    def test_a_cadence_refusal_leaves_the_original_draft_acceptable(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, _, message_id = _queue_a_follow_up(storage, ws_path, 1)
        original_id = _pending_follow_ups(storage)[0].id
        # The user tightened the cadence after the cron run queued this.
        CadencePolicy(
            days_between_touches=90, max_touches=MAX_TOUCHES, max_follow_ups_per_run=5,
        ).save(storage)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", side_effect=["r", "a"]), patch(
            "careeros.operations.follow_up.generate_follow_up_message",
        ) as mock_generate, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert "not due yet" in result.output
        # A refusal costs nothing: no LLM call, and the original approval is
        # still the one that executes.
        mock_generate.assert_not_called()
        mock_send_email.assert_called_once()
        _, _, body = mock_send_email.call_args.args
        assert body == FOLLOW_UP_DRAFT
        assert _approval_states(storage)[original_id] == "executed"
        assert OutreachMessage.load(storage, message_id).touch_count == 2

    def test_an_exhausted_cadence_refusal_is_also_survivable(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)
        original_id = _pending_follow_ups(storage)[0].id
        CadencePolicy(
            days_between_touches=DAYS_BETWEEN_TOUCHES, max_touches=1,
            max_follow_ups_per_run=5,
        ).save(storage)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", side_effect=["r", "a"]), patch(
            "careeros.operations.follow_up.generate_follow_up_message",
        ) as mock_generate, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert "Cadence exhausted" in result.output
        mock_generate.assert_not_called()
        mock_send_email.assert_called_once()
        assert _approval_states(storage)[original_id] == "executed"

    def test_a_deleted_cadence_policy_is_reported_not_a_traceback(self, tmp_path):
        """CadencePolicy.load raises FileNotFoundError, which is not an OperationError."""
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)
        original_id = _pending_follow_ups(storage)[0].id
        (tmp_path / "config" / "cadence_policy.json").unlink()

        with patch("careeros.cli.outreach_cmd.Prompt.ask", side_effect=["r", "a"]), patch(
            "careeros.operations.follow_up.generate_follow_up_message",
        ) as mock_generate, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "cadence_policy.json" in result.output
        mock_generate.assert_not_called()
        # Still acceptable afterwards: the queued draft did not depend on
        # the policy file being there.
        mock_send_email.assert_called_once()
        assert _approval_states(storage)[original_id] == "executed"

    def test_a_failed_regeneration_leaves_the_item_acceptable(self, tmp_path):
        """DraftFailed on a re-propose must not consume the queued draft."""
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)
        original_id = _pending_follow_ups(storage)[0].id

        with patch("careeros.cli.outreach_cmd.Prompt.ask", side_effect=["r", "a"]), patch(
            "careeros.operations.follow_up.generate_follow_up_message", return_value="",
        ) as mock_generate, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_generate.call_count == 1
        mock_send_email.assert_called_once()
        _, _, body = mock_send_email.call_args.args
        assert body == FOLLOW_UP_DRAFT
        assert _approval_states(storage)[original_id] == "executed"

    def test_regeneration_is_bounded_by_max_regenerations(self, tmp_path):
        """The same bound `outreach send` uses, for the same reason."""
        from careeros.cli.outreach_cmd import MAX_REGENERATIONS

        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)

        asked = []

        def _record(prompt, **kwargs):
            asked.append(prompt)
            return "r" if len(asked) <= MAX_REGENERATIONS else "s"

        with patch("careeros.cli.outreach_cmd.Prompt.ask", side_effect=_record), patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=REGENERATED_DRAFT,
        ) as mock_generate, patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_generate.call_count == MAX_REGENERATIONS
        # The last prompt no longer offers regeneration at all.
        assert "[R]egenerate" in asked[0]
        assert "[R]egenerate" not in asked[-1]


class TestReviewAuditTrail:
    def test_review_events_carry_their_own_action_label(self, tmp_path):
        """The interactive drainer is distinguishable from the cron proposer.

        Task 6 stamps "outreach-follow-up" on everything the scheduled
        command does. A reviewer sending a follow-up by hand is a different
        entrypoint, and the audit trail has to say which one acted.
        """
        assert REVIEW_CMD_ACTION_LABEL == "outreach-review"
        assert REVIEW_CMD_ACTION_LABEL != FOLLOW_UP_CMD_ACTION_LABEL

        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), patch(
            "careeros.operations.follow_up.send_email",
        ):
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        events = _log_events(storage)
        sent = [e for e in events if e["event_type"] == "follow_up_sent"]
        granted = [e for e in events if e["event_type"] == "approval_granted"]
        assert len(sent) == 1 and len(granted) == 1
        assert {e["action"] for e in sent + granted} == {REVIEW_CMD_ACTION_LABEL}
        # And it is a person at a terminal, not automation.
        assert {e["agent_runtime"] for e in sent + granted} == {"local"}
        # The proposer's own trail is untouched by this command.
        drafted = [e for e in events if e["event_type"] == "follow_up_drafted"]
        assert {e["action"] for e in drafted} == {FOLLOW_UP_CMD_ACTION_LABEL}

    def test_a_decline_is_recorded_under_the_review_label_too(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="d"), patch(
            "careeros.operations.follow_up.send_email",
        ):
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        declined = [
            e for e in _log_events(storage)
            if e["event_type"] == "follow_up_send_declined"
        ]
        assert len(declined) == 1
        assert declined[0]["action"] == REVIEW_CMD_ACTION_LABEL


class TestReviewShowsTheNumbersTheDecisionTurnsOn:
    """A third nudge after eleven days is a different call from one after three."""

    def test_the_touch_number_and_days_elapsed_are_on_screen(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(
            storage, ws_path, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
            touch_count=2,
        )

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="s"), patch(
            "careeros.operations.follow_up.send_email",
        ):
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        # The touch number rides in on the approval summary the proposer
        # wrote; the elapsed days are recoverable from nowhere else on
        # screen.
        assert "follow-up #3" in result.output
        assert str(DAYS_BETWEEN_TOUCHES + 1) + " day(s) since the last touch" in result.output

    def test_an_unusable_timestamp_costs_the_line_not_the_item(self, tmp_path):
        """The value can only have been hand-edited after the item was queued.

        check_follow_up_due refuses such a record structurally, so the
        cadence decision is safe either way; here it is a display detail,
        and the reviewer must still get to read the draft.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, _, message_id = _queue_a_follow_up(storage, ws_path, 1)
        approval_id = _pending_follow_ups(storage)[0].id
        OutreachMessage.load(storage, message_id).model_copy(
            update={"last_touched_at": "not-a-date", "sent_at": "not-a-date"},
        ).save(storage)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="s"), patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "bumping this to the top" in result.output
        assert "day(s) since the last touch" not in result.output
        mock_send_email.assert_not_called()
        assert Approval.load(storage, approval_id).state == "pending"


# A draft shaped the way an LLM actually writes one. Every bracketed span
# here is silently *deleted* by Rich's default markup parsing, so with
# markup on, the reviewer reads text that is not the text that gets mailed.
BRACKETED_DRAFT = (
    "Hi Jane, see the [posting](https://x.com/job) I mentioned. "
    "Attaching my CV [resume.pdf]. My rate is [dim] negotiable and "
    "I am [available] from June. Thanks [i] appreciate it."
)

# And a draft whose bracketed span looks like a *closing* tag. Rich raises
# rich.errors.MarkupError on this, which is not an OperationError, so it
# used to escape review's per-item guard entirely.
CLOSING_TAG_DRAFT = "Hi Jane, just following up. [/b] Best, Alice"


class TestTheTextOnScreenIsTheTextThatGetsEmailed:
    """The whole safety argument of `review` is that those two are equal.

    Rich console markup is enabled by default, so handing a raw string to
    Panel or console.print has the display interpret bracketed spans while
    execute_follow_up mails the raw stored bytes. This class pins the
    equality directly rather than asserting a bracket-free substring, which
    is what let the bug live under a green suite.
    """

    def _panel_body(self, output):
        """The draft lines lifted back out of the rendered Rich panel.

        Rich draws a box and pads to the console width, so the bytes cannot
        be compared to the draft as-is. Stripping the border characters and
        rejoining gives back the wrapped draft text, which is enough to
        assert that no character of it went missing.
        """
        lines = []
        for line in output.splitlines():
            if line.startswith("│") and line.endswith("│"):
                lines.append(line[1:-1].strip())
        return " ".join(part for part in lines if part)

    def test_a_bracketed_draft_is_displayed_exactly_as_it_is_sent(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1, draft=BRACKETED_DRAFT)

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send_email.assert_called_once()
        _, _, body = mock_send_email.call_args.args
        assert body == BRACKETED_DRAFT
        # The discriminating assertion: the bytes handed to send_email are
        # recoverable, character for character, from what was on screen.
        assert self._panel_body(result.output) == " ".join(BRACKETED_DRAFT.split())
        # And spelled out span by span, so a failure says which one vanished.
        for span in ("[posting]", "[resume.pdf]", "[dim]", "[available]", "[i]"):
            assert span in result.output

    def test_a_closing_tag_in_a_draft_does_not_abandon_the_queue(self, tmp_path):
        """MarkupError is not an OperationError, so the guard never saw it.

        Before the fix this raised out of the first item's panel render:
        item 2 was never offered, both approvals stayed pending, and no
        summary line printed.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1, draft=CLOSING_TAG_DRAFT)
        _queue_a_follow_up(storage, ws_path, 2, draft=FOLLOW_UP_DRAFT)
        queue = _pending_follow_ups(storage)
        assert len(queue) == 2
        first_id, second_id = queue[0].id, queue[1].id

        with patch(
            "careeros.cli.outreach_cmd.Prompt.ask", return_value="a",
        ) as mock_ask, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        # Both items were offered, and both sent.
        assert mock_ask.call_count == 2
        assert mock_send_email.call_count == 2
        bodies = [call.args[2] for call in mock_send_email.call_args_list]
        assert CLOSING_TAG_DRAFT in bodies
        # The tag reached the screen as text rather than being parsed.
        assert "[/b]" in result.output
        # And the summary — the thing a MarkupError escape destroyed — printed.
        assert "2 sent" in result.output
        states = _approval_states(storage)
        assert states[first_id] == "executed"
        assert states[second_id] == "executed"

    def test_a_bracketed_title_and_summary_do_not_abandon_the_queue(self, tmp_path):
        """The panel title and the approval summary are external data too.

        The summary the proposer wrote embeds job.company and job.title
        straight off a scraped posting, and the panel title is built from a
        researched Person's name. A closing-tag-shaped span in either raised
        MarkupError from those two print calls, exactly as one in the draft
        did — so both are rendered verbatim now.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        # Seeded and then edited *before* the proposer runs, so the bracketed
        # company name is baked into the approval summary it writes.
        job_id, person_id, _ = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
            company_name="Acme [/b] Corp",
        )
        Person.load(storage, person_id).model_copy(
            update={"name": "Jane [/i] Doe"},
        ).save(storage)
        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ):
            assert runner.invoke(
                outreach_app, ["follow-up", "--workspace", ws_path],
            ).exit_code == 0
        assert "[/b]" in _pending_follow_ups(storage)[0].summary

        with patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="s"), patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert result.exit_code == 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        mock_send_email.assert_not_called()
        assert "Acme [/b] Corp" in result.output
        assert "Jane [/i] Doe" in result.output
        assert "1 skipped" in result.output


class TestReviewDrivesTheRealPrompt:
    """Every other review test patches Prompt.ask with a Mock.

    A Mock never looks at `choices=` or `default=`, so four
    safety-relevant mutations shipped green: flipping the main prompt's
    default from "s" to "a" (a stray Enter mails instead of skipping),
    deleting the choices list entirely (no input validation at all),
    re-adding "r" to the withdrawn prompt's choices (the regeneration bound
    becomes bypassable), and flipping the withdrawn prompt's default to "a"
    (a stray Enter mails at the bound). These tests feed real keystrokes
    through CliRunner(input=...) instead, the way test_browse_cmd.py and
    test_job_cmd.py already do, so the prompt's own arguments are executed.

    Rich echoes the choices and the default into its prompt line as
    "[a/r/d/s] (s):", which is why those strings are asserted directly:
    they are the only on-screen evidence of the two arguments.
    """

    def test_a_bare_enter_skips_rather_than_sending(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)
        approval_id = _pending_follow_ups(storage)[0].id

        with patch("careeros.operations.follow_up.send_email") as mock_send_email:
            result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="\n",
            )

        assert result.exit_code == 0
        # The load-bearing assertion: an unbidden prompt on a queue drainer
        # must not mail someone you want a referral from on a stray Enter.
        mock_send_email.assert_not_called()
        assert "1 skipped" in result.output
        assert Approval.load(storage, approval_id).state == "pending"
        # And the default is visibly skip, not accept.
        assert "[a/r/d/s] (s)" in result.output

    def test_an_invalid_choice_is_rejected_and_re_prompted(self, tmp_path):
        """Without `choices=`, "x" falls through the s/r/d branches and sends."""
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)
        approval_id = _pending_follow_ups(storage)[0].id

        with patch("careeros.operations.follow_up.send_email") as mock_send_email:
            result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="x\ns\n",
            )

        assert result.exit_code == 0
        mock_send_email.assert_not_called()
        assert "Please select one of the available options" in result.output
        assert "1 skipped" in result.output
        assert Approval.load(storage, approval_id).state == "pending"

    def test_r_is_genuinely_refused_once_the_bound_is_reached(self, tmp_path):
        """The bound is enforced by `choices`, not by the prompt's wording.

        The pre-existing bound test asserted only that the label no longer
        reads "[R]egenerate", so re-adding "r" to the withdrawn prompt's
        choices shipped green. Here the sixth "r" is a real keystroke: it
        has to be rejected, and the Enter after it has to skip rather than
        send.
        """
        from careeros.cli.outreach_cmd import MAX_REGENERATIONS

        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)
        keystrokes = "r\n" * MAX_REGENERATIONS + "r\n" + "\n"

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=REGENERATED_DRAFT,
        ) as mock_generate, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send_email:
            result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input=keystrokes,
            )

        assert result.exit_code == 0
        # Exactly the bound's worth of paid LLM calls, and no more.
        assert mock_generate.call_count == MAX_REGENERATIONS
        assert "Please select one of the available options" in result.output
        # The withdrawn prompt offers three options and still defaults to skip.
        assert "[a/d/s] (s)" in result.output
        # The Enter that followed the refused "r" skipped; it did not mail.
        mock_send_email.assert_not_called()
        assert "1 skipped" in result.output

    def test_accepting_through_the_real_prompt_still_sends(self, tmp_path):
        """The counterpart to the three refusals above.

        Without this, a `choices` list that rejected *everything* would also
        pass the tests above, so the accept keystroke has to be pinned too.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)

        with patch("careeros.operations.follow_up.send_email") as mock_send_email:
            result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="a\n",
            )

        assert result.exit_code == 0
        mock_send_email.assert_called_once()
        assert mock_send_email.call_args.args[2] == FOLLOW_UP_DRAFT
        assert "1 sent" in result.output


class TestReviewWiresItsCollaborators:
    """Three currently-correct behaviours with no guard of their own."""

    def test_a_regenerate_is_stamped_with_the_review_label(self, tmp_path):
        """Not the proposer's label — a redraft asked for by a human is a
        different entrypoint from the cron job that queued the original, and
        the audit log's first question is which one acted.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)

        with patch("careeros.operations.follow_up.generate_follow_up_message",
                   return_value=REGENERATED_DRAFT), patch(
            "careeros.operations.follow_up.send_email",
        ):
            result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="r\na\n",
            )

        assert result.exit_code == 0
        drafted = [
            e for e in _log_events(storage) if e["event_type"] == "follow_up_drafted"
        ]
        assert len(drafted) == 2
        # The cron run drafted the first; the review loop drafted the second.
        assert drafted[0]["action"] == FOLLOW_UP_CMD_ACTION_LABEL
        assert drafted[1]["action"] == REVIEW_CMD_ACTION_LABEL
        # The approval the redraft opened is stamped the same way.
        requested = [
            e for e in _log_events(storage) if e["event_type"] == "approval_requested"
        ]
        assert requested[-1]["action"] == REVIEW_CMD_ACTION_LABEL

    def test_the_model_override_reaches_the_re_propose(self, tmp_path):
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _queue_a_follow_up(storage, ws_path, 1)

        with patch("careeros.operations.follow_up.generate_follow_up_message",
                   return_value=REGENERATED_DRAFT) as mock_generate, patch(
            "careeros.operations.follow_up.send_email",
        ):
            result = runner.invoke(
                outreach_app,
                ["review", "--workspace", ws_path, "--model", "some/cheap-model"],
                input="r\na\n",
            )

        assert result.exit_code == 0
        assert mock_generate.call_args.kwargs["model"] == "some/cheap-model"

    def test_the_recipient_shown_comes_from_the_person_record(self, tmp_path):
        """A raw person id on screen is not a name a reviewer can decide on.

        The fallback to the id when people/<id>.json is gone is covered
        elsewhere; this pins the ordinary case, where always returning the
        id would otherwise ship green.
        """
        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, person_id, _ = _queue_a_follow_up(storage, ws_path, 1)
        Person.load(storage, person_id).model_copy(
            update={"name": "Priya Raman"},
        ).save(storage)

        with patch("careeros.operations.follow_up.send_email"):
            result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="s\n",
            )

        assert result.exit_code == 0
        assert "Follow-up to Priya Raman" in result.output
        assert "Follow-up to " + person_id not in result.output


class TestAStrandedApprovedApprovalCannotSendTwice:
    """An `approved` approval that never executed used to be a second email.

    execute_follow_up refuses a missing recipient *before* mark_executed, on
    purpose, so the refusal stays retryable — which leaves the record
    `approved`. list_pending does not report it, so `outreach review` cannot
    see it, and open_approval used to supersede only `pending`, so the next
    scheduled run left it `approved` alongside a fresh approval. If that
    re-draft came back byte-identical, the stranded approval's draft_sha256
    still matched the stored draft, so executing it sent the follow-up a
    second time.
    """

    def test_a_re_propose_supersedes_the_stranded_approval(self, tmp_path):
        from careeros.operations._shared import digest_text
        from careeros.operations.approvals import payload_value
        from careeros.operations.errors import ApprovalNotGranted
        from careeros.operations.follow_up import execute_follow_up
        from careeros.runtime.factory import open_local_runtime

        ws_path, storage = _setup_workspace(tmp_path, cadence=_default_cadence())
        _, person_id, message_id = _seed_relationship(
            storage, 1, days_since_touch=DAYS_BETWEEN_TOUCHES + 1,
        )
        # No address on file, so the accept below refuses pre-send.
        Person.load(storage, person_id).model_copy(
            update={"email": None},
        ).save(storage)

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ):
            assert runner.invoke(
                outreach_app, ["follow-up", "--workspace", ws_path],
            ).exit_code == 0
        stranded_id = _pending_follow_ups(storage)[0].id

        with patch("careeros.operations.follow_up.send_email") as mock_send_email:
            result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="a\n",
            )
        assert result.exit_code == 1
        mock_send_email.assert_not_called()
        # Stranded: decided, not executed, and invisible to the drainer.
        assert Approval.load(storage, stranded_id).state == "approved"
        assert _pending_follow_ups(storage) == []

        # The operator adds the address and the next cron run comes round.
        Person.load(storage, person_id).model_copy(
            update={"email": "jane@acme.com"},
        ).save(storage)
        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value=FOLLOW_UP_DRAFT,
        ):
            assert runner.invoke(
                outreach_app, ["follow-up", "--workspace", ws_path],
            ).exit_code == 0
        fresh = _pending_follow_ups(storage)
        assert len(fresh) == 1 and fresh[0].id != stranded_id

        # The precondition that made this a live double-send rather than an
        # ArtifactChanged: the redraft is byte-identical, so the stranded
        # approval's digest still matches what is on disk.
        stranded = Approval.load(storage, stranded_id)
        assert payload_value(stranded, "draft_sha256") == digest_text(
            OutreachMessage.load(storage, message_id).draft_text,
        )
        # And it is now superseded, so it cannot execute at all.
        assert stranded.state == "superseded"
        runtime = open_local_runtime(storage)
        with patch("careeros.operations.follow_up.send_email") as mock_second:
            with pytest.raises(ApprovalNotGranted):
                execute_follow_up(
                    runtime, stranded_id, action_label=REVIEW_CMD_ACTION_LABEL,
                )
        mock_second.assert_not_called()

        # The fresh approval still sends, exactly once.
        with patch("careeros.operations.follow_up.send_email") as mock_third:
            result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="a\n",
            )
        assert result.exit_code == 0
        assert mock_third.call_count == 1
