from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.outreach_cmd import outreach_app, people_app
from careeros.core.models import Company, Job, OutreachMessage, Person, PolicyConfig, Profile
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
