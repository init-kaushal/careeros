from __future__ import annotations
import uuid
from careeros.runtime.claude_code import ApprovalCallback, ClaudeCodeRuntime
from careeros.runtime.local import LocalRuntime
from careeros.runtime.automation import AutomationRuntime
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import open_workspace


def open_local_runtime(storage: StorageProvider, session_id: str | None = None) -> LocalRuntime:
    ctx = open_workspace(storage)
    return LocalRuntime(storage, ctx, session_id or uuid.uuid4().hex)


def open_claude_code_runtime(
    storage: StorageProvider,
    approval_callback: ApprovalCallback,
    session_id: str | None = None,
) -> ClaudeCodeRuntime:
    ctx = open_workspace(storage)
    return ClaudeCodeRuntime(storage, ctx, session_id or uuid.uuid4().hex, approval_callback)


def open_automation_runtime(storage: StorageProvider, session_id: str | None = None) -> AutomationRuntime:
    ctx = open_workspace(storage)
    return AutomationRuntime(storage, ctx, session_id or uuid.uuid4().hex)
