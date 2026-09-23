from unittest.mock import MagicMock, patch
from careeros.core.activity import ActivityEvent
from careeros.runtime.automation import AutomationRuntime
from careeros.runtime.base import ActionProposal, ApprovalResult


def _make_event(agent_runtime="unset", session_id="unset"):
    return ActivityEvent(
        event_type="job_applied", action="apply", status="success",
        summary="Auto-applied", agent_runtime=agent_runtime, session_id=session_id,
    )


class TestAutomationRuntime:
    def test_read_workspace_decodes_bytes(self):
        storage = MagicMock()
        storage.read.return_value = b"hello"
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        assert runtime.read_workspace("profile/profile.json") == "hello"
        storage.read.assert_called_once_with("profile/profile.json")

    def test_write_workspace_encodes_and_atomic_writes(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        runtime.write_workspace("notes/note.txt", "hello world")
        storage.atomic_write.assert_called_once_with("notes/note.txt", b"hello world")

    def test_request_approval_always_approves(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        proposal = ActionProposal(action="apply_to_job", summary="Auto-apply to Acme?")
        result = runtime.request_approval(proposal)
        assert result == ApprovalResult(approved=True, reason="auto-approved by automation policy")

    def test_request_approval_always_approves_regardless_of_proposal_content(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        proposal = ActionProposal(action="anything", summary="Anything at all", entity_type="job", entity_id="x")
        result = runtime.request_approval(proposal)
        assert result.approved is True

    def test_auto_approve_is_still_the_default_when_no_callback_is_passed(self):
        # The default is load-bearing for discover-and-apply, which passes
        # no callback and relies on this staying an unconditional yes.
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-1")
        assert runtime._approval_callback is None
        result = runtime.request_approval(ActionProposal(action="apply_to_job", summary="?"))
        assert result == ApprovalResult(approved=True, reason="auto-approved by automation policy")

    def test_a_passed_callback_is_the_one_consulted(self):
        storage = MagicMock()
        ctx = MagicMock()
        seen = []

        def _deny(proposal):
            seen.append(proposal)
            return ApprovalResult(approved=False, reason="nope")

        runtime = AutomationRuntime(storage, ctx, session_id="sess-1", approval_callback=_deny)
        proposal = ActionProposal(action="send_follow_up", summary="Send follow-up?")
        result = runtime.request_approval(proposal)

        # The callback, not the auto-approve default, decided — and it saw
        # the proposal rather than being called blind.
        assert result == ApprovalResult(approved=False, reason="nope")
        assert seen == [proposal]

    def test_record_activity_stamps_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        event = _make_event(agent_runtime="unset", session_id="unset")
        with patch("careeros.runtime.automation.ActivityLogger") as MockLogger:
            runtime = AutomationRuntime(storage, ctx, session_id="sess-42")
            runtime.record_activity(event)
        MockLogger.return_value.log.assert_called_once_with(event)
        assert event.agent_runtime == "automation"
        assert event.session_id == "sess-42"

    def test_new_event_sets_agent_runtime_and_session_id(self):
        storage = MagicMock()
        ctx = MagicMock()
        runtime = AutomationRuntime(storage, ctx, session_id="sess-7")
        event = runtime.new_event("job_applied", "apply", "Auto-applied", entity_type="job", entity_id="j1")
        assert event.event_type == "job_applied"
        assert event.agent_runtime == "automation"
        assert event.session_id == "sess-7"
        assert event.entity_type == "job"
        assert event.entity_id == "j1"
