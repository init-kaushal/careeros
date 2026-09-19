from __future__ import annotations
from rich.prompt import Confirm
from careeros.core.activity import ActivityEvent, ActivityLogger
from careeros.runtime.base import ActionProposal, ApprovalResult
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import WorkspaceContext


class LocalRuntime:
    agent_runtime_name = "local"

    def __init__(self, storage: StorageProvider, ctx: WorkspaceContext, session_id: str) -> None:
        self.storage = storage
        self.ctx = ctx
        self.session_id = session_id
        self._logger = ActivityLogger(storage, session_id=session_id)

    def read_workspace(self, path: str) -> str:
        return self.storage.read(path).decode()

    def write_workspace(self, path: str, content: str) -> None:
        self.storage.atomic_write(path, content.encode())

    def request_approval(self, proposal: ActionProposal) -> ApprovalResult:
        approved = Confirm.ask(proposal.summary, default=False)
        return ApprovalResult(approved=approved)

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
