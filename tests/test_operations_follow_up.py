from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from careeros.core.models import (
    Approval, CadencePolicy, Company, Job, OutreachMessage, Person, PolicyConfig,
    Profile,
)
from careeros.operations.approvals import (
    APPROVED, DECLINED, EXECUTED, FAILED, PENDING, SUPERSEDED, resolve_approval,
)
from careeros.operations.errors import (
    ApprovalNotGranted, ArtifactChanged, CadenceExhausted, DraftFailed, EntityNotFound,
    MalformedApproval, MalformedTouchTimestamp, MissingRecipient, NotDueForFollowUp,
    PolicyBlocked, RelationshipClosed, SendFailed, WrongApprovalAction,
)
from careeros.operations.follow_up import (
    check_follow_up_due, decline_follow_up, execute_follow_up, propose_follow_up,
)
from careeros.operations.outreach import make_message_id
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_local_runtime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
MESSAGE_ID = make_message_id(JOB_ID, PERSON_ID)
PRIOR_DRAFT = "Hi Jane, I saw the Senior SRE role at Acme Corp..."
FOLLOW_UP_DRAFT = "Hi Jane, just bumping this to the top of your inbox."

DAYS_BETWEEN_TOUCHES = 5
MAX_TOUCHES = 3


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _runtime(tmp_path, with_email=True, session_id="sess-1"):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
           title="Engineering Manager",
           email=("jane@acme.com" if with_email else None),
           researched_at=now).save(storage)
    CadencePolicy(
        days_between_touches=DAYS_BETWEEN_TOUCHES, max_touches=MAX_TOUCHES,
        max_follow_ups_per_run=5,
    ).save(storage)
    return open_local_runtime(storage, session_id=session_id)


def _seed_message(storage, **overrides):
    now = datetime.now(timezone.utc).isoformat()
    fields = dict(
        id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID, draft_text=PRIOR_DRAFT,
        send_state="sent", referral_state="research", created_at=now,
        sent_at=_iso(DAYS_BETWEEN_TOUCHES + 2),
        last_touched_at=_iso(DAYS_BETWEEN_TOUCHES + 2), touch_count=1,
    )
    fields.update(overrides)
    OutreachMessage(**fields).save(storage)


def _propose(runtime, draft=FOLLOW_UP_DRAFT, model=None):
    with patch("careeros.operations.follow_up.generate_follow_up_message", return_value=draft):
        return propose_follow_up(
            runtime, JOB_ID, PERSON_ID, model=model, action_label="follow_up",
        )


def _log(storage):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return storage.read("activity/" + date + ".jsonl").decode()


def _approve(runtime, approval_id, reason="user said yes"):
    return resolve_approval(
        runtime, approval_id, ApprovalResult(approved=True, reason=reason),
        action_label="follow_up",
    )


def _decline(runtime, approval_id, reason="user said no"):
    return resolve_approval(
        runtime, approval_id, ApprovalResult(approved=False, reason=reason),
        action_label="follow_up",
    )


class TestProposeFollowUpHappyPath:
    def test_writes_the_draft_logs_and_opens_a_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)

        proposal = _propose(runtime)

        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.draft_text == FOLLOW_UP_DRAFT
        assert "follow_up_drafted" in _log(runtime.storage)

        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == PENDING
        assert set(approval.payload.keys()) == {
            "message_id", "job_id", "person_id", "draft_sha256", "touch_number",
            "subject",
        }
        assert approval.payload["message_id"] == MESSAGE_ID
        assert approval.payload["job_id"] == JOB_ID
        assert approval.payload["person_id"] == PERSON_ID
        assert approval.payload["touch_number"] == "2"
        assert approval.payload["subject"] == proposal.subject

        assert proposal.message_id == MESSAGE_ID
        assert proposal.draft_text == FOLLOW_UP_DRAFT
        assert proposal.recipient_name == "Jane Doe"
        assert proposal.recipient_email == "jane@acme.com"
        assert proposal.touch_number == 2

    def test_leaves_sent_at_and_touch_count_alone(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        original = OutreachMessage.load(runtime.storage, MESSAGE_ID)

        _propose(runtime)

        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.sent_at == original.sent_at
        assert message.last_touched_at == original.last_touched_at
        assert message.touch_count == original.touch_count

    def test_re_proposing_supersedes_the_prior_pending_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)

        first = _propose(runtime)
        second = _propose(runtime, draft="A completely different bump.")

        assert Approval.load(runtime.storage, first.approval_id).state == SUPERSEDED
        assert Approval.load(runtime.storage, second.approval_id).state == PENDING
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == (
            "A completely different bump."
        )


class TestNotDueForFollowUp:
    def test_raises_with_correct_day_counts_and_writes_nothing(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(
            runtime.storage,
            last_touched_at=_iso(1), sent_at=_iso(1), touch_count=1,
        )

        with pytest.raises(NotDueForFollowUp) as exc:
            _propose(runtime)

        assert exc.value.message_id == MESSAGE_ID
        assert exc.value.days_since == 1
        assert exc.value.days_required == DAYS_BETWEEN_TOUCHES

        # No approval ever appeared, and the draft was never rewritten.
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == PRIOR_DRAFT

    def test_legacy_message_sent_before_cadence_fields_existed_counts_as_touch_one(
        self, tmp_path
    ):
        """A message sent before this phase has sent_at set but
        last_touched_at=None and touch_count=0 (their model defaults).
        Reading those fields raw would permanently exclude it from follow-up.
        It must be treated as one real touch, due once days_between_touches
        have passed since sent_at, with the next follow-up numbered 2.
        """
        runtime = _runtime(tmp_path)
        _seed_message(
            runtime.storage,
            sent_at=_iso(DAYS_BETWEEN_TOUCHES + 1),
            last_touched_at=None, touch_count=0,
        )

        proposal = _propose(runtime)

        assert proposal.touch_number == 2

    def test_a_never_sent_message_is_refused(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(
            runtime.storage, sent_at=None, last_touched_at=None, touch_count=0,
        )

        with pytest.raises(NotDueForFollowUp):
            _propose(runtime)

        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []


class TestCadenceExhausted:
    def test_raises_at_the_cap(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(
            runtime.storage,
            touch_count=MAX_TOUCHES, last_touched_at=_iso(DAYS_BETWEEN_TOUCHES + 5),
        )

        with pytest.raises(CadenceExhausted) as exc:
            _propose(runtime)

        assert exc.value.message_id == MESSAGE_ID
        assert exc.value.touch_count == MAX_TOUCHES
        assert exc.value.max_touches == MAX_TOUCHES
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []


class TestRelationshipClosed:
    @pytest.mark.parametrize("referral_state", ["referral_confirmed", "closed"])
    def test_terminal_referral_state_is_refused(self, tmp_path, referral_state):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage, referral_state=referral_state)

        with pytest.raises(RelationshipClosed) as exc:
            _propose(runtime)

        assert exc.value.message_id == MESSAGE_ID
        assert exc.value.reason == referral_state
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []

    def test_a_set_closed_reason_is_refused(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage, closed_reason="person left the company")

        with pytest.raises(RelationshipClosed) as exc:
            _propose(runtime)

        assert exc.value.reason == "person left the company"
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []


class TestCadencePolicyMissing:
    def test_propagates_file_not_found(self, tmp_path):
        storage = LocalFilesystemStorage(str(tmp_path))
        init_workspace(storage)
        now = datetime.now(timezone.utc).isoformat()
        Profile(name="Alice Smith", email="alice@example.com").save(storage)
        Job(id=JOB_ID, source="browse", url="https://example.com/job",
            company="Acme Corp", title="Senior SRE", stage="saved",
            created_at=now, updated_at=now).save(storage)
        Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
        Person(id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
               email="jane@acme.com", researched_at=now).save(storage)
        _seed_message(storage)
        runtime = open_local_runtime(storage, session_id="sess-1")
        # No CadencePolicy saved.

        with pytest.raises(FileNotFoundError):
            _propose(runtime)


class TestCheckFollowUpDue:
    """The single shared due-ness checker.

    It used to exist twice: inlined in propose_follow_up as early raises and
    mirrored as a bool in careeros/cli/outreach_cmd.py. Two sources of truth
    for the one rule that decides whether this project spends an LLM call
    and interrupts the user.
    """

    def _policy(self, **overrides):
        fields = dict(
            days_between_touches=DAYS_BETWEEN_TOUCHES, max_touches=MAX_TOUCHES,
            max_follow_ups_per_run=5,
        )
        fields.update(overrides)
        return CadencePolicy(**fields)

    def _message(self, **overrides):
        now = datetime.now(timezone.utc).isoformat()
        fields = dict(
            id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID, draft_text=PRIOR_DRAFT,
            send_state="sent", created_at=now,
            sent_at=_iso(DAYS_BETWEEN_TOUCHES + 2),
            last_touched_at=_iso(DAYS_BETWEEN_TOUCHES + 2), touch_count=1,
        )
        fields.update(overrides)
        return OutreachMessage(**fields)

    def test_a_due_message_returns_what_it_computed(self):
        status = check_follow_up_due(
            self._message(), self._policy(), datetime.now(timezone.utc),
        )
        # Returned rather than left for the caller to recompute: a second
        # datetime.now() for the same record is exactly the drift that put
        # the CLI filter and the operation out of step.
        assert status.days_since == DAYS_BETWEEN_TOUCHES + 2
        assert status.effective_touch_count == 1

    def test_now_is_honoured_rather_than_read_from_the_clock(self):
        # Same record, an earlier `now`: not due. If the checker called
        # datetime.now() itself this could not be expressed at all, and a
        # batch caller could not hold one instant across its whole run.
        message = self._message()
        earlier = datetime.now(timezone.utc) - timedelta(days=DAYS_BETWEEN_TOUCHES)

        with pytest.raises(NotDueForFollowUp) as exc:
            check_follow_up_due(message, self._policy(), earlier)

        assert exc.value.days_since == 2

    @pytest.mark.parametrize("bad_value", [
        "not-a-date", "2026-01-01T10:00:00", "2026-09-01",
    ])
    def test_an_unusable_last_touch_is_a_structured_refusal(self, bad_value):
        # Neither a raw ValueError nor a raw TypeError may escape the
        # operations layer: a caller enumerating many relationships has to
        # be able to skip the one bad record.
        with pytest.raises(MalformedTouchTimestamp) as exc:
            check_follow_up_due(
                self._message(last_touched_at=bad_value), self._policy(),
                datetime.now(timezone.utc),
            )

        assert exc.value.message_id == MESSAGE_ID
        assert exc.value.value == bad_value

    def test_a_closed_relationship_with_an_unusable_timestamp_is_just_closed(self):
        # The parse happens only where a comparison is needed, so a terminal
        # relationship stays a plain skip rather than becoming an error.
        with pytest.raises(RelationshipClosed):
            check_follow_up_due(
                self._message(referral_state="closed", last_touched_at="not-a-date"),
                self._policy(), datetime.now(timezone.utc),
            )

    def test_the_refusals_keep_their_structured_attributes(self):
        # The reason it raises instead of returning a bool: every caller can
        # word its own message, and docs/agent-integration.md documents all
        # three of these.
        with pytest.raises(CadenceExhausted) as exhausted:
            check_follow_up_due(
                self._message(touch_count=MAX_TOUCHES), self._policy(),
                datetime.now(timezone.utc),
            )
        assert exhausted.value.touch_count == MAX_TOUCHES
        assert exhausted.value.max_touches == MAX_TOUCHES

        with pytest.raises(RelationshipClosed) as closed:
            check_follow_up_due(
                self._message(closed_reason="person left the company"),
                self._policy(), datetime.now(timezone.utc),
            )
        assert closed.value.reason == "person left the company"


class TestProposeUsesTheSharedChecker:
    def test_an_explicit_now_reaches_the_due_check(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        earlier = datetime.now(timezone.utc) - timedelta(days=DAYS_BETWEEN_TOUCHES)

        with patch("careeros.operations.follow_up.generate_follow_up_message") as mock_gen:
            with pytest.raises(NotDueForFollowUp):
                propose_follow_up(
                    runtime, JOB_ID, PERSON_ID, action_label="follow_up", now=earlier,
                )

        mock_gen.assert_not_called()

    def test_entity_not_found_still_precedes_the_due_check(self, tmp_path):
        """Ordering preserved deliberately through the extraction.

        Checking due-ness first would flip a not-due orphan record from an
        error into a silent skip. That might be an improvement, but it is
        not one this refactor is entitled to make as a side effect.
        """
        runtime = _runtime(tmp_path)
        # Not due (touched today) *and* orphaned (no job record).
        _seed_message(runtime.storage, last_touched_at=_iso(0), sent_at=_iso(0))
        runtime.storage.delete("jobs/" + JOB_ID + ".json")

        with pytest.raises(EntityNotFound):
            _propose(runtime)


class TestEntityNotFound:
    def test_a_missing_person_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        runtime.storage.delete("people/" + PERSON_ID + ".json")

        with pytest.raises(EntityNotFound):
            _propose(runtime)

    def test_no_prior_outreach_message_raises_entity_not_found(self, tmp_path):
        runtime = _runtime(tmp_path)
        # No OutreachMessage seeded at all.

        with pytest.raises(EntityNotFound):
            _propose(runtime)


class TestPolicyBlocked:
    def test_logs_then_raises_without_attempting_the_draft(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        PolicyConfig(blocked_companies=["Acme Corp"]).save(runtime.storage)

        with patch("careeros.operations.follow_up.generate_follow_up_message") as mock_gen:
            with pytest.raises(PolicyBlocked) as exc:
                propose_follow_up(
                    runtime, JOB_ID, PERSON_ID, action_label="follow_up",
                )

        assert exc.value.rule == "blocked_company:Acme Corp"
        mock_gen.assert_not_called()
        assert "policy_blocked" in _log(runtime.storage)
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []
        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == PRIOR_DRAFT


class TestDraftFailed:
    def test_an_empty_draft_raises_draft_failed(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)

        with pytest.raises(DraftFailed):
            _propose(runtime, draft="")

        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).draft_text == PRIOR_DRAFT
        assert [p for p in runtime.storage.list("approvals/") if p.endswith(".json")] == []


# subject_for(job) = "Re: " + outreach.subject_for(job); job seeded above has
# title="Senior SRE", company="Acme Corp".
EXPECTED_SUBJECT = "Re: Regarding Senior SRE at Acme Corp"


class TestExecuteFollowUp:
    def test_sends_the_approved_draft_and_marks_everything_sent(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        with patch("careeros.operations.follow_up.send_email") as mock_send:
            result = execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        mock_send.assert_called_once_with(
            "jane@acme.com", EXPECTED_SUBJECT, FOLLOW_UP_DRAFT
        )
        assert result.message_id == MESSAGE_ID
        assert result.recipient_name == "Jane Doe"
        assert result.sent_at
        assert result.touch_count == 2

        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.send_state == "sent"
        assert message.sent_at == result.sent_at
        assert Approval.load(runtime.storage, proposal.approval_id).state == EXECUTED
        assert "follow_up_sent" in _log(runtime.storage)

    def test_a_successful_send_sets_last_touched_at_and_increments_touch_count(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        with patch("careeros.operations.follow_up.send_email"):
            result = execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.last_touched_at == result.sent_at
        assert message.touch_count == 2

    @pytest.mark.parametrize(
        "state", [PENDING, DECLINED, SUPERSEDED, FAILED, EXECUTED]
    )
    def test_refuses_any_state_but_approved(self, tmp_path, state):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        approval.model_copy(update={"state": state}).save(runtime.storage)
        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(ApprovalNotGranted):
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")
        mock_send.assert_not_called()
        # Verify the refusal did not mutate the approval's state.
        assert Approval.load(runtime.storage, proposal.approval_id).state == state

    def test_refuses_an_approval_belonging_to_a_different_action(self, tmp_path):
        # An approval id is an opaque cross-process string; nothing else would
        # stop execute_follow_up from acting on an approval minted by a
        # different action. Must be caught before any state change, including
        # before mark_executed.
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        approval.model_copy(update={"action": "send_outreach"}).save(runtime.storage)
        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(WrongApprovalAction) as exc:
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")
        assert exc.value.expected == "send_follow_up"
        assert exc.value.actual == "send_outreach"
        mock_send.assert_not_called()
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_refuses_when_the_draft_changed_after_approval(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        # Simulate an out-of-band edit that superseding cannot catch.
        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        message.model_copy(update={"draft_text": "something else entirely"}).save(runtime.storage)

        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(ArtifactChanged):
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")
        mock_send.assert_not_called()
        # Nothing was attempted, so the approval must stay approved and retryable.
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_a_malformed_payload_raises_rather_than_key_error(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        approval.model_copy(update={"payload": {}}).save(runtime.storage)
        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(MalformedApproval):
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")
        mock_send.assert_not_called()

    def test_refuses_without_a_recipient_email(self, tmp_path):
        runtime = _runtime(tmp_path, with_email=False)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(MissingRecipient) as exc:
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")
        assert exc.value.person_name == "Jane Doe"
        mock_send.assert_not_called()
        # Nothing was attempted, so adding the address and retrying must work
        # without re-approving.
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_refuses_to_send_to_a_relationship_closed_after_approval(self, tmp_path):
        """A closed relationship is refused at the point of action, not only
        at propose time.

        The approval is reached the production way: a pre-send refusal (no
        address on file) deliberately leaves it `approved` and retryable, and
        that is precisely how an authorization comes to outlive the
        relationship it was granted against. The close itself is out of band —
        `careeros outreach close` supersedes the approvals it can see, but an
        approval an agent minted before the user closed the record by some
        other route is stopped only here.
        """
        runtime = _runtime(tmp_path, with_email=False)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(MissingRecipient):
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")
        mock_send.assert_not_called()
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

        # The user supplies the missing address — and ends the relationship.
        person = Person.load(runtime.storage, PERSON_ID)
        person.model_copy(update={"email": "jane@acme.com"}).save(runtime.storage)
        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        message.model_copy(update={
            "referral_state": "closed", "closed_reason": "took another offer",
        }).save(runtime.storage)

        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(RelationshipClosed) as exc:
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        # The recorded reason, not the bare state: it is what the refusal is
        # for, and a caller has to be able to word its own message.
        assert exc.value.reason == "took another offer"
        mock_send.assert_not_called()
        # Before any state change, so a relationship closed by mistake and
        # re-opened leaves the authorization intact.
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED
        assert "follow_up_sent" not in _log(runtime.storage)

    @pytest.mark.parametrize(
        "update, expected_reason",
        [
            ({"referral_state": "closed"}, "closed"),
            ({"referral_state": "referral_confirmed"}, "referral_confirmed"),
            ({"closed_reason": "they ghosted me"}, "they ghosted me"),
        ],
    )
    def test_every_terminal_relationship_marker_refuses_the_send(
        self, tmp_path, update, expected_reason,
    ):
        """The whole terminal-state rule binds the send path, not just the
        `closed` half of it.

        Same disjunction check_follow_up_due applies, because it is literally
        the same function: referral_confirmed is terminal too, and a
        closed_reason on its own is terminal even if referral_state was never
        moved.
        """
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        message.model_copy(update=update).save(runtime.storage)

        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(RelationshipClosed) as exc:
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        assert exc.value.reason == expected_reason
        mock_send.assert_not_called()
        assert Approval.load(runtime.storage, proposal.approval_id).state == APPROVED

    def test_smtp_failure_marks_the_message_and_approval_failed(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        with patch(
            "careeros.operations.follow_up.send_email",
            side_effect=RuntimeError("smtp says: bad creds for alice@example.com"),
        ):
            with pytest.raises(SendFailed):
                execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        assert OutreachMessage.load(runtime.storage, MESSAGE_ID).send_state == "failed"
        approval = Approval.load(runtime.storage, proposal.approval_id)
        assert approval.state == FAILED
        # Exception type name only — never the raw message, which can quote
        # the SMTP server's response and cannot be scrubbed from an
        # append-only activity log.
        assert approval.detail == "RuntimeError"
        log = _log(runtime.storage)
        assert "follow_up_send_failed" in log
        assert "bad creds" not in log

    def test_mark_executed_happens_before_the_send(self, tmp_path):
        """Verify the approval is consumed before the external action.

        This is the ordering a Phase 12a final review flagged as Critical
        for execute_outreach_send: consuming the approval after the send
        leaves it approved and retryable for the whole SMTP conversation, so
        a crash or a second concurrent process could send twice. Consuming it
        first means a crash mid-send leaves a stale `executed` record with an
        unsent message — recoverable, unlike a duplicate email.
        """
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        def check_approval_executed(*args, **kwargs):
            # When send_email is called, the approval must already be EXECUTED.
            approval = Approval.load(runtime.storage, proposal.approval_id)
            assert approval.state == EXECUTED

        with patch(
            "careeros.operations.follow_up.send_email",
            side_effect=check_approval_executed,
        ):
            execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")

    def test_subject_is_bound_to_the_approval_not_recomputed_from_the_job(self, tmp_path):
        """Editing the job after approval must not change the sent subject.

        The subject is recorded on the payload at propose time and read back
        verbatim at execute time — it is not hashed and digest-compared like
        the draft body; it is stored on the payload directly, which is a
        stronger guarantee than digest-binding: an out-of-band edit to
        job.title cannot change what goes out. Mirrors
        execute_outreach_send's own binding, and the Critical finding
        (Phase 12a's final review) that made it necessary there.
        """
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        job = Job.load(runtime.storage, JOB_ID)
        job.model_copy(update={"title": "A Completely Different Title"}).save(runtime.storage)

        with patch("careeros.operations.follow_up.send_email") as mock_send:
            execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        mock_send.assert_called_once_with(
            "jane@acme.com", EXPECTED_SUBJECT, FOLLOW_UP_DRAFT
        )

    def test_a_missing_job_at_execute_time_does_not_block_the_send(self, tmp_path):
        """execute_follow_up never re-derives the subject, so it never needs
        the Job. A job file deleted (or otherwise missing) between approval
        and execution must not block sending an already-approved follow-up.
        """
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)

        runtime.storage.delete("jobs/" + JOB_ID + ".json")

        with patch("careeros.operations.follow_up.send_email") as mock_send:
            result = execute_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        mock_send.assert_called_once_with(
            "jane@acme.com", EXPECTED_SUBJECT, FOLLOW_UP_DRAFT
        )
        assert result.recipient_name == "Jane Doe"


class TestDeclineFollowUp:
    def test_marks_the_message_declined_and_logs(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _decline(runtime, proposal.approval_id)

        decline_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.send_state == "declined"
        assert "follow_up_send_declined" in _log(runtime.storage)

    def test_advances_last_touched_at_but_leaves_touch_count_alone(self, tmp_path):
        # The whole subtlety of decline: it defers the cadence (advances
        # last_touched_at) without counting as a real touch (touch_count is
        # unchanged), so a user who declines every time never exhausts
        # max_touches — a deliberate tradeoff; `careeros outreach close` is
        # the explicit off switch.
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        original = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        proposal = _propose(runtime)
        _decline(runtime, proposal.approval_id)

        decline_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        # Advanced, not merely different: a regression that wrote an *earlier*
        # timestamp here would leave the relationship past-due and pass a
        # plain `!=` assertion, which is exactly the bug this defers.
        assert datetime.fromisoformat(message.last_touched_at) > datetime.fromisoformat(
            original.last_touched_at
        )
        assert message.touch_count == original.touch_count

    def test_refuses_an_approval_belonging_to_a_different_action(self, tmp_path):
        # Worse here than for outreach: this decline writes last_touched_at,
        # a scheduling field, so a transposed send_outreach id would defer
        # the wrong relationship's cadence by a full period. The two payloads
        # share message_id and person_id, so nothing else refuses it.
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        original = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        proposal = _propose(runtime)
        _decline(runtime, proposal.approval_id)
        approval = Approval.load(runtime.storage, proposal.approval_id)
        approval.model_copy(update={"action": "send_outreach"}).save(runtime.storage)

        with pytest.raises(WrongApprovalAction) as exc:
            decline_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        assert exc.value.expected == "send_follow_up"
        assert exc.value.actual == "send_outreach"
        # Nothing was mutated — in particular the cadence was not deferred.
        message = OutreachMessage.load(runtime.storage, MESSAGE_ID)
        assert message.last_touched_at == original.last_touched_at
        assert message.send_state == original.send_state
        assert "follow_up_send_declined" not in _log(runtime.storage)

    def test_refuses_when_the_approval_was_not_declined(self, tmp_path):
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _approve(runtime, proposal.approval_id)
        with pytest.raises(ApprovalNotGranted):
            decline_follow_up(runtime, proposal.approval_id, action_label="follow_up")

    def test_declining_defers_the_cadence_instead_of_letting_it_be_reproposed(self, tmp_path):
        """The consequence that makes the last_touched_at advance load-bearing.

        Without it, the relationship stays past-due and propose_follow_up
        would immediately re-propose the very follow-up the user just
        declined. With it, the decline defers by one full cadence period.
        """
        runtime = _runtime(tmp_path)
        _seed_message(runtime.storage)
        proposal = _propose(runtime)
        _decline(runtime, proposal.approval_id)

        decline_follow_up(runtime, proposal.approval_id, action_label="follow_up")

        with pytest.raises(NotDueForFollowUp):
            _propose(runtime)
