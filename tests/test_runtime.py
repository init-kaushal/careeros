import pytest
from unittest.mock import MagicMock, patch
from careeros.core.activity import ActivityEvent
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.runtime.local import LocalRuntime


def _make_event(agent_runtime="unset", session_id="unset"):
    return ActivityEvent(
        event_type="job_added", action="browse", status="success",
        summary="Job saved", agent_runtime=agent_runtime, session_id=session_id,
    )


class TestLocalRuntime:
    def test_read_workspace_decodes_bytes(self):
        storage = MagicMock()
        storage.read.return_value = b"hello"
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-1")
        assert runtime.read_workspace("profile/profile.json") == "hello"
        storage.read.assert_called_once_with("profile/profile.json")

    def test_write_workspace_encodes_and_atomic_writes(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-1")
        runtime.write_workspace("notes/note.txt", "hello world")
        storage.atomic_write.assert_called_once_with("notes/note.txt", b"hello world")

    def test_request_approval_calls_confirm_ask_with_summary(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-1")
        proposal = ActionProposal(action="apply_to_job", summary="Apply to Acme?")
        with patch("careeros.runtime.local.Confirm.ask", return_value=True) as mock_ask:
            result = runtime.request_approval(proposal)
        mock_ask.assert_called_once_with("Apply to Acme?", default=False)
        assert result == ApprovalResult(approved=True)

    def test_request_approval_returns_approved_false_when_declined(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-1")
        proposal = ActionProposal(action="apply_to_job", summary="Apply to Acme?")
        with patch("careeros.runtime.local.Confirm.ask", return_value=False):
            result = runtime.request_approval(proposal)
        assert result.approved is False

    def test_record_activity_stamps_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        event = _make_event(agent_runtime="unset", session_id="unset")
        with patch("careeros.runtime.local.ActivityLogger") as MockLogger:
            runtime = LocalRuntime(storage, ctx, session_id="sess-42")
            runtime.record_activity(event)
        MockLogger.return_value.log.assert_called_once_with(event)
        assert event.agent_runtime == "local"
        assert event.session_id == "sess-42"

    def test_new_event_sets_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = LocalRuntime(storage, ctx, session_id="sess-7")
        event = runtime.new_event("job_added", "browse", "Job saved", entity_type="job", entity_id="j1")
        assert event.event_type == "job_added"
        assert event.agent_runtime == "local"
        assert event.session_id == "sess-7"
        assert event.entity_type == "job"
        assert event.entity_id == "j1"
