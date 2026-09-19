from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from careeros.core.activity import ActivityEvent
from careeros.storage.interface import StorageProvider


@dataclass
class ActionProposal:
    action: str
    summary: str
    entity_type: str | None = None
    entity_id: str | None = None


@dataclass
class ApprovalResult:
    approved: bool
    reason: str | None = None


class AgentRuntime(Protocol):
    storage: StorageProvider

    def read_workspace(self, path: str) -> str: ...
    def write_workspace(self, path: str, content: str) -> None: ...
    def request_approval(self, proposal: ActionProposal) -> ApprovalResult: ...
    def record_activity(self, event: ActivityEvent) -> None: ...
    def new_event(
        self, event_type: str, action: str, summary: str, status: str = "success", **kwargs
    ) -> ActivityEvent: ...
