from __future__ import annotations
from rich.prompt import Confirm
from rich.text import Text
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
        # Text(), not the raw string: this is the approval gate, so the one
        # thing it must not do is show the user something other than what it
        # is asking about. Rich console markup is on by default, and a summary
        # is built from scraped data — a person's name, a company, a job
        # title. Two things were happening here. A bracketed span was
        # silently *deleted* from the prompt, so "Jane [dim]Doe" asked about
        # "Jane Doe" and the user approved a summary that did not match the
        # record; and a closing-tag-shaped span raised MarkupError, taking
        # down outreach send, apply and outreach connect before the question
        # was ever asked. A Text instance carries no markup by definition.
        # Same reasoning as careeros/cli/_display.py's verbatim(), which is
        # not imported here because runtime must not depend on cli.
        approved = Confirm.ask(Text(proposal.summary), default=False)
        reason = (
            "approved at a terminal confirmation prompt" if approved
            else "declined at a terminal confirmation prompt"
        )
        return ApprovalResult(approved=approved, reason=reason)

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
