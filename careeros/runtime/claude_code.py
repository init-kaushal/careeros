from __future__ import annotations
from typing import Callable
from careeros.core.activity import ActivityEvent, ActivityLogger
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import WorkspaceContext

ApprovalCallback = Callable[[ActionProposal], ApprovalResult]


class ClaudeCodeRuntime:
    agent_runtime_name = "claude_code"

    def __init__(
        self,
        storage: StorageProvider,
        ctx: WorkspaceContext,
        session_id: str,
        approval_callback: ApprovalCallback,
    ) -> None:
        self.storage = storage
        self.ctx = ctx
        self.session_id = session_id
        self._approval_callback = approval_callback
        self._logger = ActivityLogger(storage, session_id=session_id)

    def read_workspace(self, path: str) -> str:
        return self.storage.read(path).decode()

    def write_workspace(self, path: str, content: str) -> None:
        self.storage.atomic_write(path, content.encode())

    def request_approval(self, proposal: ActionProposal) -> ApprovalResult:
        return self._approval_callback(proposal)

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
