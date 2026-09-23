from __future__ import annotations
from careeros.core.activity import ActivityEvent, ActivityLogger
from careeros.runtime.base import ActionProposal, ApprovalCallback, ApprovalResult
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import WorkspaceContext


class AutomationRuntime:
    agent_runtime_name = "automation"

    def __init__(
        self,
        storage: StorageProvider,
        ctx: WorkspaceContext,
        session_id: str,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        self.storage = storage
        self.ctx = ctx
        self.session_id = session_id
        # Optional, and None means the auto-approve default below. That
        # default is the right one for discover-and-apply, the one command in
        # this project where a wrong decision only costs an application, and
        # it is what every existing caller gets by passing nothing. A caller
        # that proposes and stops — `outreach follow-up` — passes queue_only
        # instead so an unwired approval path denies rather than silently
        # saying yes. Carried as a constructor slot rather than patched onto
        # the instance so the choice is visible where the runtime is opened.
        self._approval_callback = approval_callback
        self._logger = ActivityLogger(storage, session_id=session_id)

    def read_workspace(self, path: str) -> str:
        return self.storage.read(path).decode()

    def write_workspace(self, path: str, content: str) -> None:
        self.storage.atomic_write(path, content.encode())

    def request_approval(self, proposal: ActionProposal) -> ApprovalResult:
        if self._approval_callback is not None:
            return self._approval_callback(proposal)
        return ApprovalResult(approved=True, reason="auto-approved by automation policy")

    def record_activity(self, event: ActivityEvent) -> None:
        event.agent_runtime = self.agent_runtime_name
        event.session_id = self.session_id
        self._logger.log(event)

    def new_event(
        self, event_type: str, action: str, summary: str, status: str = "success", **kwargs
    ) -> ActivityEvent:
        return self._logger.new_event(
            event_type, action, summary, status=status, agent_runtime=self.agent_runtime_name, **kwargs
        )
