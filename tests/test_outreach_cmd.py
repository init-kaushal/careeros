from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from careeros.cli.outreach_cmd import outreach_app, people_app
from careeros.core.models import Company, Job, OutreachMessage, Person, Profile
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
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=False), \
             patch("careeros.cli.outreach_cmd.send_email") as mock_send:
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
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.cli.outreach_cmd.send_email") as mock_send:
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
        assert "outreach_send_approved" in log_content
        assert "outreach_sent" in log_content

    def test_approved_without_email_blocks_send_and_does_not_log_failed(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=False)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.cli.outreach_cmd.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 1
        mock_send.assert_not_called()
        storage = LocalFilesystemStorage(ws_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "outreach_send_approved" in log_content
        assert "outreach_send_failed" not in log_content

    def test_smtp_failure_logs_send_failed(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=True)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.cli.outreach_cmd.send_email", side_effect=Exception("smtp error")):
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 1
        storage = LocalFilesystemStorage(ws_path)
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.send_state == "failed"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_content = storage.read("activity/" + today + ".jsonl").decode()
        assert "outreach_send_failed" in log_content

    def test_draft_generation_failure_exits_1(self, tmp_path):
        ws_path = _setup_workspace(tmp_path)
        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value=""):
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

        with patch("careeros.cli.outreach_cmd.generate_outreach_message", return_value="Follow-up hi Jane..."), \
             patch("careeros.cli.outreach_cmd.Prompt.ask", return_value="a"), \
             patch("careeros.runtime.local.Confirm.ask", return_value=True), \
             patch("careeros.cli.outreach_cmd.send_email") as mock_send:
            result = runner.invoke(outreach_app, ["send", "--job", JOB_ID, "--person", PERSON_ID, "--workspace", ws_path])

        assert result.exit_code == 0
        mock_send.assert_called_once()
        message = OutreachMessage.load(storage, MESSAGE_ID)
        assert message.referral_state == "referral_requested"
        assert message.send_state == "sent"
        assert message.sent_at is not None


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


class TestPeopleUpdate:
    def test_updates_email(self, tmp_path):
        ws_path = _setup_workspace(tmp_path, with_email=False)
        result = runner.invoke(people_app, ["update", PERSON_ID, "--email", "jane@acme.com", "--workspace", ws_path])
        assert result.exit_code == 0
        storage = LocalFilesystemStorage(ws_path)
        person = Person.load(storage, PERSON_ID)
        assert person.email == "jane@acme.com"
