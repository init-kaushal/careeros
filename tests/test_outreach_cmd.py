import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from careeros.cli.outreach_cmd import outreach_app, people_app
from careeros.core.models import (
    Approval, CadencePolicy, Company, Job, OutreachMessage, Person, PolicyConfig,
    Profile,
)
from careeros.operations.approvals import (
    APPROVED, DECLINED, PENDING, SUPERSEDED, list_by_state, list_pending,
    open_approval, resolve_approval,
)
from careeros.operations.errors import ApprovalNotGranted, RelationshipClosed
from careeros.operations.follow_up import ACTION as FOLLOW_UP_ACTION
from careeros.operations.follow_up import execute_follow_up, propose_follow_up
from careeros.operations.outreach import ACTION as OUTREACH_ACTION
from careeros.runtime.base import ApprovalResult
from careeros.runtime.factory import open_local_runtime, resolve_storage
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace

runner = CliRunner()

JOB_ID = "acme-sre-abc1"
COMPANY_ID = "acme-corp"
PERSON_ID = "acme-corp-jane-doe"
MESSAGE_ID = JOB_ID + "__" + PERSON_ID


def _setup_workspace(tmp_path, with_email=True):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    now = datetime.now(timezone.utc).isoformat()
    Profile(name="Alice Smith", email="alice@example.com", title="Senior SRE").save(storage)
    Job(
        id=JOB_ID, source="browse", url="https://example.com/job",
        company="Acme Corp", title="Senior SRE", stage="saved",
        created_at=now, updated_at=now,
    ).save(storage)
    Company(id=COMPANY_ID, name="Acme Corp", researched_at=now).save(storage)
    Person(
        id=PERSON_ID, company_id=COMPANY_ID, name="Jane Doe", role_category="em",
        title="Engineering Manager", email=("jane@acme.com" if with_email else None),
        researched_at=now,
    ).save(storage)
    return str(tmp_path)


def _outreach_events(storage):
    """Today's activity log parsed into events, or [] when there is none."""
    return [json.loads(line) for line in _outreach_log(storage).splitlines() if line]


def _outreach_log(storage):
    """Today's activity log, or "" when nothing has been logged at all.

    Tolerates the missing file so a test can assert an event is *absent*
    without the assertion depending on some other event having created the
    log first.
    """
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = "activity/" + date + ".jsonl"
    if not storage.exists(path):
        return ""
    return storage.read(path).decode()


class TestOutreachSend:
    def test_declined_approval_logs_declined_and_does_not_send(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=False), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send.assert_not_called()
        storage = LocalFilesystemStorage(ws_path)
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.send_state == "declined"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "outreach_send_declined" in log_content

    def test_approved_with_email_sends_and_logs_sent(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=True)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        assert call_args[0] == "jane@acme.com"
        storage = LocalFilesystemStorage(ws_path)
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.send_state == "sent"
        assert message.sent_at is not None
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "approval_requested" in log_content
        assert "approval_granted" in log_content
        assert "outreach_sent" in log_content
        # The recipient's email is PII beyond what's needed for audit --
        # entity_id already identifies the person; the address itself
        # must never land in the append-only activity log.
        assert "jane@acme.com" not in log_content

    def test_approved_without_email_blocks_send_and_does_not_log_failed(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=False)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 1
        mock_send.assert_not_called()
        # The remediation instruction is what makes this recoverable rather
        # than a dead end: it must name the exact command and the person id
        # to run it against. Not an exact full-line match -- Rich wraps and
        # styles this output, so pin the load-bearing substring instead.
        assert "careeros people update " + PERSON_ID in result.output
        storage = LocalFilesystemStorage(ws_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "approval_requested" in log_content
        assert "approval_granted" in log_content
        assert "outreach_send_failed" not in log_content
        assert "outreach_sent" not in log_content

    def test_smtp_failure_logs_send_failed(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=True)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email", side_effect=Exception("smtp error")):
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 1
        storage = LocalFilesystemStorage(ws_path)
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.send_state == "failed"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "outreach_send_failed" in log_content
        # The old inline command printed "Send failed: <detail>" -- that
        # prefix must survive the move to SendFailed's own message.
        assert "Send failed: smtp error" in result.output

    def test_draft_generation_failure_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value=""):
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])
        assert result.exit_code == 1

    def test_resend_preserves_existing_referral_state(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=True)
        storage = LocalFilesystemStorage(ws_path)
        now = datetime.now(timezone.utc).isoformat()
        OutreachMessage(
            id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID,
            draft_text="Hi Jane...", send_state="sent", referral_state="referral_requested",
            created_at=now, sent_at=now,
        ).save(storage)

        with patch("careeros.operations.outreach.generate_outreach_message", return_value="Follow-up hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send.assert_called_once()
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.referral_state == "referral_requested"
        assert message.send_state == "sent"
        assert message.sent_at is not None
        # A re-send must be surfaced to the human approving it, not silent.
        assert "already sent" in result.output.lower()

    def test_quitting_at_review_leaves_no_pending_approval(self, tmp_path):
        from careeros.core.models import Approval
        from careeros.operations.approvals import list_pending

        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="q"), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        assert "Aborted." in result.output
        mock_send.assert_not_called()
        storage = LocalFilesystemStorage(ws_path)
        # No pending approval survives a quit: it must be resolved to a
        # terminal state, not left open for a later process to act on.
        assert list_pending(storage) == []
        approvals = [p for p in storage.list("approvals/") if p.endswith(".json")]
        assert len(approvals) == 1
        approval_id = approvals[0][len("approvals/"):-len(".json")]
        approval = Approval.load(storage, approval_id)
        assert approval.state == "declined"
        assert approval.reason == "aborted at review"
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.send_state == "declined"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "approval_declined" in log_content
        assert "outreach_send_declined" in log_content

    def test_policy_blocked_company_exits_1_and_does_not_send(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws_path)
        PolicyConfig(blocked_companies=["Acme Corp"]).save(storage)
        with patch("careeros.operations.outreach.generate_outreach_message") as mock_gen, \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 1
        assert "blocked by policy" in result.output.lower()
        mock_gen.assert_not_called()
        mock_send.assert_not_called()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "policy_blocked" in log_content


class TestMarkReferralRequested:
    def test_sets_referral_state_and_logs(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws_path)
        now = datetime.now(timezone.utc).isoformat()
        OutreachMessage(
            id=MESSAGE_ID, job_id=JOB_ID, person_id=PERSON_ID,
            draft_text="Hi Jane...", send_state="sent", created_at=now,
        ).save(storage)

        result = runner.invoke(outreach_app, ["mark-referral-requested", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.referral_state == "referral_requested"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "referral_requested" in log_content

    def test_unsafe_job_and_person_values_are_slugified_not_raised(self, tmp_path):
        # --job/--person are raw CLI input; a value like "../../etc" must be
        # slugified into a safe path segment rather than escaping the
        # outreach/ directory or surfacing storage's ValueError as an
        # unhandled traceback.
        ws_path = _setup_workspace(tmp_path)
        storage = LocalFilesystemStorage(ws_path)
        now = datetime.now(timezone.utc).isoformat()
        OutreachMessage(
            id="acme-sre-abc1__acme-corp-jane-doe", job_id="../../acme-sre-abc1", person_id="../../acme-corp-jane-doe",
            draft_text="Hi Jane...", send_state="sent", created_at=now,
        ).save(storage)

        result = runner.invoke(outreach_app, [
            "mark-referral-requested", "--job", "../../acme-sre-abc1", "--person", "../../acme-corp-jane-doe", "--workspace", ws_path,
        ])

        assert result.exit_code == 0
        assert not (Path(ws_path) / "etc").exists()
        message = OutreachMessage.load(storage, "acme-sre-abc1__acme-corp-jane-doe")
        assert message.referral_state == "referral_requested"


class TestPeopleUpdate:
    def test_updates_email(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=False)
        result = runner.invoke(people_app, ["update", PERSON_ID, "--email", "jane@acme.com", "--workspace", ws_path])
        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        person = Person.load(storage, PERSON_ID)
        assert person.email == "jane@acme.com"


class TestOutreachSendApprovalRecord:
    def test_an_approved_send_leaves_an_executed_approval_record(self, tmp_path):
        from careeros.core.models import Approval
        from careeros.operations.approvals import EXECUTED, list_pending
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email"):
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        approvals = [p for p in storage.list("approvals/") if p.endswith(".json")]
        assert len(approvals) == 1
        approval_id = approvals[0][len("approvals/"):-len(".json")]
        approval = Approval.load(storage, approval_id)
        assert approval.state == EXECUTED
        assert approval.decided_by == "local"
        assert list_pending(storage) == []

    def test_regenerating_supersedes_and_sends_only_the_accepted_draft(self, tmp_path):
        from careeros.core.models import OutreachMessage
        ws_path = _setup_workspace(tmp_path)
        drafts = ["first draft", "second draft"]
        with patch("careeros.operations.outreach.generate_outreach_message", side_effect=drafts), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", side_effect=["r", "a"]), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        # The draft that was on screen when the user accepted is the one sent.
        assert mock_send.call_args[0][2] == "second draft"
        storage = LocalFilesystemStorage(ws_path)
        assert OutreachMessage.load(storage, MESSAGE_ID).draft_text == "second draft"


class TestSendShowsTheBytesItSends:
    """`outreach send` had the same latent Rich-markup bug as `review`.

    Rich console markup is on by default, so a bracketed span in a draft
    handed to Panel is deleted from the display (or, if it looks like a
    closing tag, raises MarkupError) while execute_outreach_send mails the
    raw stored bytes. `review` is where it mattered most, but the class of
    bug is the same and so is the fix.
    """

    BRACKETED = (
        "Hi Jane, see the [posting](https://x.com/job) I mentioned. "
        "Attaching my CV [resume.pdf]. I am [available] from June."
    )

    def test_a_bracketed_draft_is_displayed_as_it_is_sent(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.operations.outreach.generate_outreach_message", return_value=self.BRACKETED), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        assert mock_send.call_args[0][2] == self.BRACKETED
        # Every bracketed span survived to the screen. Asserted span by span
        # rather than as one substring because Rich wraps the panel body.
        for span in ("[posting]", "[resume.pdf]", "[available]"):
            assert span in result.output

    def test_a_closing_tag_in_a_draft_is_not_a_traceback(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        draft = "Hi Jane, following up. [/b] Best, Alice"
        with patch("careeros.operations.outreach.generate_outreach_message", return_value=draft), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.operations.outreach.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "[/b]" in result.output
        assert mock_send.call_args[0][2] == draft


class TestOutreachClose:
    """`careeros outreach close` — the explicit off switch for a cadence.

    Seeds a relationship that is genuinely due for a follow-up (a real
    CadencePolicy plus a last touch older than days_between_touches), so the
    "closed relationships are refused" assertions below cannot pass merely
    because the relationship was never eligible in the first place.
    """

    DAYS_BETWEEN_TOUCHES = 5
    REASON = "took another offer"

    OTHER_JOB_ID = "acme-sre-xyz9"
    OTHER_PERSON_ID = "acme-corp-john-roe"
    OTHER_MESSAGE_ID = OTHER_JOB_ID + "__" + OTHER_PERSON_ID

    def _setup(self, tmp_path, *, days_since_touch=10, with_email=True, **message_kwargs):
        ws_path = _setup_workspace(tmp_path, with_email=with_email)
        storage = LocalFilesystemStorage(ws_path)
        CadencePolicy(
            days_between_touches=self.DAYS_BETWEEN_TOUCHES, max_touches=3,
            max_follow_ups_per_run=5,
        ).save(storage)
        now = datetime.now(timezone.utc)
        touch_at = (now - timedelta(days=days_since_touch)).isoformat()
        fields = {
            "id": MESSAGE_ID, "job_id": JOB_ID, "person_id": PERSON_ID,
            "draft_text": "Hi Jane...", "send_state": "sent",
            "created_at": now.isoformat(), "sent_at": touch_at,
            "last_touched_at": touch_at, "touch_count": 1,
        }
        fields.update(message_kwargs)
        OutreachMessage(**fields).save(storage)
        return ws_path, storage

    def _close(self, ws_path, reason=REASON):
        return runner.invoke(outreach_app, [
            "close", "--job", JOB_ID, "--person", PERSON_ID,
            "--reason", reason, "--workspace", ws_path,
        ])

    def _queue_a_follow_up(self, ws_path):
        """Queue a pending follow-up the way the scheduled proposer does.

        Built by running the real `outreach follow-up` rather than
        hand-writing an Approval, so the payload keys, the draft digest and
        the action are whatever propose_follow_up actually produces — a
        hand-seeded record could be ignored by `review` for a reason that
        has nothing to do with what these tests are asserting.
        """
        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value="Hi Jane, just bumping this.",
        ), patch("careeros.operations.follow_up.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])
        assert result.exit_code == 0, result.output
        mock_send.assert_not_called()
        storage = LocalFilesystemStorage(ws_path)
        pending = list_pending(storage)
        assert len(pending) == 1, "fixture did not queue a follow-up"
        assert pending[0].action == FOLLOW_UP_ACTION
        return pending[0]

    def test_close_sets_state_and_reason_and_logs_cadence_closed(self, tmp_path):
        ws_path, storage = self._setup(tmp_path)

        result = self._close(ws_path)

        assert result.exit_code == 0, result.output
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.referral_state == "closed"
        assert message.closed_reason == self.REASON
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        events = [
            json.loads(line)
            for line in storage.read("activity/" + today + ".jsonl").decode().splitlines()
            if line
        ]
        closed = [e for e in events if e["event_type"] == "cadence_closed"]
        assert len(closed) == 1
        # The reason is the whole point of the command, so it has to reach
        # the append-only log and not just the mutable record.
        assert self.REASON in closed[0]["summary"]
        assert closed[0]["entity_id"] == MESSAGE_ID
        # Structured as well as prose, so a reader does not have to parse the
        # summary to recover the reason.
        assert closed[0]["reason"] == self.REASON
        # Setting referral_state="closed" overwrites what it was, so the
        # append-only log is the only surviving record of the prior state.
        assert "(was research)" in closed[0]["summary"]

    def test_a_closed_relationship_is_refused_by_propose_follow_up(self, tmp_path):
        ws_path, storage = self._setup(tmp_path)
        assert self._close(ws_path).exit_code == 0

        runtime = open_local_runtime(resolve_storage(ws_path))
        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
        ) as mock_draft, pytest.raises(RelationshipClosed) as excinfo:
            propose_follow_up(runtime, JOB_ID, PERSON_ID, action_label="test")

        assert excinfo.value.reason == self.REASON
        # Refused before the LLM is reached, so a closed relationship costs
        # nothing to skip.
        mock_draft.assert_not_called()

    def test_the_scheduled_run_no_longer_proposes_a_closed_relationship(self, tmp_path):
        ws_path, storage = self._setup(tmp_path)
        assert self._close(ws_path).exit_code == 0

        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value="Hi Jane, just bumping this.",
        ) as mock_draft, patch(
            "careeros.operations.follow_up.send_email",
        ) as mock_send:
            result = runner.invoke(outreach_app, ["follow-up", "--workspace", ws_path])

        assert result.exit_code == 0, result.output
        mock_draft.assert_not_called()
        mock_send.assert_not_called()
        assert list_pending(storage) == []
        assert "Due: 0" in result.output

    def test_missing_outreach_message_exits_1_with_a_message(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)

        result = self._close(ws_path)

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "No outreach message" in result.output

    def test_a_whitespace_only_reason_is_refused_and_writes_nothing(self, tmp_path):
        ws_path, storage = self._setup(tmp_path)

        result = self._close(ws_path, reason="   ")

        assert result.exit_code == 1
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.referral_state == "research"
        assert message.closed_reason is None
        # Nothing happened, so nothing may be logged as having happened.
        assert "cadence_closed" not in _outreach_log(storage)

    def test_the_reason_is_stored_stripped(self, tmp_path):
        ws_path, storage = self._setup(tmp_path)

        assert self._close(ws_path, reason="  took another offer\n").exit_code == 0

        assert OutreachMessage.load(storage, MESSAGE_ID).closed_reason == self.REASON

    def test_closing_an_already_closed_relationship_keeps_the_first_reason(self, tmp_path):
        ws_path, storage = self._setup(
            tmp_path, referral_state="closed", closed_reason="hired elsewhere",
        )

        result = self._close(ws_path, reason="changed my mind")

        assert result.exit_code == 1
        # The recorded reason is the record this command exists to create,
        # so a second close must not silently overwrite it.
        assert OutreachMessage.load(storage, MESSAGE_ID).closed_reason == "hired elsewhere"
        assert "hired elsewhere" in result.output
        assert "changed my mind" not in _outreach_log(storage)

    def test_closing_declines_a_queued_follow_up_so_review_cannot_send_it(self, tmp_path):
        ws_path, storage = self._setup(tmp_path)
        approval = self._queue_a_follow_up(ws_path)

        assert self._close(ws_path).exit_code == 0

        # Taken out of the queue through the state machine's only legal exit
        # from pending, so `review` cannot offer it and execute_follow_up
        # (which requires approved) can never act on it.
        assert Approval.load(storage, approval.id).state == DECLINED
        assert list_pending(storage) == []
        assert list_by_state(storage, APPROVED) == []

        with patch("careeros.operations.follow_up.send_email") as mock_send:
            review_result = runner.invoke(outreach_app, ["review", "--workspace", ws_path])

        assert review_result.exit_code == 0, review_result.output
        mock_send.assert_not_called()
        assert "No follow-ups are waiting for review." in review_result.output

    def test_review_offers_the_queued_follow_up_when_nothing_was_closed(self, tmp_path):
        """The control for the test above: the fixture really is reviewable.

        Without this, a `close` that did nothing at all to the queued
        approval would still look correct if `review` happened to ignore the
        seeded record for some unrelated reason.
        """
        ws_path, storage = self._setup(tmp_path)
        self._queue_a_follow_up(ws_path)

        # A real prompt, driven by stdin: patching Prompt.ask with a Mock
        # would skip `choices=` and `default=` entirely.
        with patch("careeros.operations.follow_up.send_email") as mock_send:
            review_result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="s\n",
            )

        assert review_result.exit_code == 0, review_result.output
        mock_send.assert_not_called()
        assert "just bumping this" in review_result.output
        assert "1 skipped" in review_result.output

    def test_close_records_the_reason_on_the_declined_approval(self, tmp_path):
        ws_path, storage = self._setup(tmp_path)
        approval = self._queue_a_follow_up(ws_path)

        assert self._close(ws_path).exit_code == 0

        # Why that specific queued draft was thrown away is recoverable from
        # the approval record itself, not only from the activity log.
        declined = Approval.load(storage, approval.id)
        assert declined.reason is not None
        assert self.REASON in declined.reason

    def _seed_other_relationship(self, storage, *, days_since_touch=10):
        """A second, unrelated relationship that is also due for a follow-up.

        Closing one relationship must not touch another's queued draft, and
        with only one relationship in the workspace that guarantee is
        indistinguishable from declining everything pending.
        """
        now = datetime.now(timezone.utc)
        touch_at = (now - timedelta(days=days_since_touch)).isoformat()
        Job(
            id=self.OTHER_JOB_ID, source="browse", url="https://example.com/other",
            company="Acme Corp", title="Staff SRE", stage="saved",
            created_at=now.isoformat(), updated_at=now.isoformat(),
        ).save(storage)
        Person(
            id=self.OTHER_PERSON_ID, company_id=COMPANY_ID, name="John Roe",
            role_category="em", title="Director", email="john@acme.com",
            researched_at=now.isoformat(),
        ).save(storage)
        OutreachMessage(
            id=self.OTHER_MESSAGE_ID, job_id=self.OTHER_JOB_ID,
            person_id=self.OTHER_PERSON_ID, draft_text="Hi John...",
            send_state="sent", created_at=now.isoformat(), sent_at=touch_at,
            last_touched_at=touch_at, touch_count=1,
        ).save(storage)

    def test_closing_one_relationship_leaves_anothers_queued_follow_up_alone(self, tmp_path):
        ws_path, storage = self._setup(tmp_path)
        self._seed_other_relationship(storage)
        with patch(
            "careeros.operations.follow_up.generate_follow_up_message",
            return_value="Hi there, just bumping this.",
        ), patch("careeros.operations.follow_up.send_email"):
            assert runner.invoke(
                outreach_app, ["follow-up", "--workspace", ws_path],
            ).exit_code == 0
        queued = {a.entity_id: a.id for a in list_pending(storage)}
        assert set(queued) == {MESSAGE_ID, self.OTHER_MESSAGE_ID}

        assert self._close(ws_path).exit_code == 0

        assert Approval.load(storage, queued[MESSAGE_ID]).state == DECLINED
        other = Approval.load(storage, queued[self.OTHER_MESSAGE_ID])
        assert other.state == PENDING
        assert [a.entity_id for a in list_pending(storage)] == [self.OTHER_MESSAGE_ID]

    def test_closing_leaves_a_pending_approval_from_another_flow_alone(self, tmp_path):
        """A pending send_outreach against the same message is not this
        command's to decide: it belongs to the initial-message flow, carries
        a different payload shape, and `close` filters on the action for
        exactly that reason.
        """
        ws_path, storage = self._setup(tmp_path)
        runtime = open_local_runtime(resolve_storage(ws_path))
        other_flow = open_approval(
            runtime, OUTREACH_ACTION, "Send the first message?",
            {"message_id": MESSAGE_ID, "job_id": JOB_ID, "person_id": PERSON_ID},
            entity_type="outreach_message", entity_id=MESSAGE_ID,
            action_label="outreach",
        )

        assert self._close(ws_path).exit_code == 0

        assert Approval.load(storage, other_flow.id).state == PENDING

    def _strand_an_approved_follow_up(self, ws_path, storage):
        """Leave an approval `approved` but unsent, the way production does.

        The reviewer accepts, and execute_follow_up refuses *before*
        attempting anything because the person has no address on file — a
        refusal that deliberately leaves the approval approved and
        retryable. Reached through the real review loop rather than by
        hand-writing an Approval, so the state is the one the code actually
        produces.
        """
        approval = self._queue_a_follow_up(ws_path)
        with patch("careeros.operations.follow_up.send_email") as mock_send:
            review_result = runner.invoke(
                outreach_app, ["review", "--workspace", ws_path], input="a\n",
            )
        assert review_result.exit_code == 1, review_result.output
        mock_send.assert_not_called()
        assert Approval.load(storage, approval.id).state == APPROVED
        return approval

    def test_closing_supersedes_an_approved_but_unsent_follow_up(self, tmp_path):
        """The stranded record is tidied, not left reading `approved`.

        Nothing can send it either way — execute_follow_up refuses a closed
        relationship at the point of action — but an authorization that can
        never be acted on must not still read `approved` in the audit trail,
        and `outreach review` reports exactly this set as stranded work the
        user might otherwise go chasing. `superseded` is the transition
        open_approval already applies to this same approved-and-unexecuted
        case, so no new state was invented for it.
        """
        ws_path, storage = self._setup(tmp_path, with_email=False)
        approval = self._strand_an_approved_follow_up(ws_path, storage)

        result = self._close(ws_path)

        assert result.exit_code == 0, result.output
        assert OutreachMessage.load(storage, MESSAGE_ID).referral_state == "closed"
        assert Approval.load(storage, approval.id).state == SUPERSEDED
        assert list_by_state(storage, APPROVED) == []
        assert "Superseded 1" in result.output

        # Why a granted authorization was revoked is recoverable from the
        # log, not inferred from whatever happens to be logged next to it.
        events = _outreach_events(storage)
        superseded = [e for e in events if e["event_type"] == "approval_superseded"]
        assert len(superseded) == 1
        assert superseded[0]["action"] == "outreach-close"
        assert self.REASON in superseded[0]["summary"]
        assert superseded[0]["reason"] == "cadence closed: " + self.REASON

    def test_a_superseded_follow_up_can_no_longer_be_executed(self, tmp_path):
        """The consequence that matters: the id is inert afterwards."""
        ws_path, storage = self._setup(tmp_path, with_email=False)
        approval = self._strand_an_approved_follow_up(ws_path, storage)
        assert self._close(ws_path).exit_code == 0

        runtime = open_local_runtime(resolve_storage(ws_path))
        with patch("careeros.operations.follow_up.send_email") as mock_send:
            with pytest.raises(ApprovalNotGranted):
                execute_follow_up(runtime, approval.id, action_label="test")
        mock_send.assert_not_called()

    def test_closing_leaves_another_relationships_approved_follow_up_alone(self, tmp_path):
        """The supersede is scoped the same way the decline is.

        With only one relationship in the workspace, "supersedes the right
        record" is indistinguishable from "supersedes everything approved".
        """
        ws_path, storage = self._setup(tmp_path, with_email=False)
        # Stranded first: _queue_a_follow_up runs the real scheduled command
        # and asserts it queued exactly one draft, which a second due
        # relationship in the workspace would break.
        mine = self._strand_an_approved_follow_up(ws_path, storage)
        self._seed_other_relationship(storage)
        runtime = open_local_runtime(resolve_storage(ws_path))
        # An approved approval for the *other* relationship, and an approved
        # approval for a different action against the one being closed.
        other = open_approval(
            runtime, FOLLOW_UP_ACTION, "Send follow-up to John?",
            {"message_id": self.OTHER_MESSAGE_ID, "person_id": self.OTHER_PERSON_ID},
            entity_type="outreach_message", entity_id=self.OTHER_MESSAGE_ID,
            action_label="test",
        )
        same_entity_other_action = open_approval(
            runtime, OUTREACH_ACTION, "Send the first message?",
            {"message_id": MESSAGE_ID, "person_id": PERSON_ID},
            entity_type="outreach_message", entity_id=MESSAGE_ID,
            action_label="test",
        )
        for seeded in (other, same_entity_other_action):
            resolve_approval(
                runtime, seeded.id, ApprovalResult(approved=True, reason="yes"),
                action_label="test",
            )

        assert self._close(ws_path).exit_code == 0

        assert Approval.load(storage, mine.id).state == SUPERSEDED
        assert Approval.load(storage, other.id).state == APPROVED
        assert Approval.load(storage, same_entity_other_action.id).state == APPROVED
        assert sorted(a.id for a in list_by_state(storage, APPROVED)) == sorted(
            [other.id, same_entity_other_action.id]
        )
