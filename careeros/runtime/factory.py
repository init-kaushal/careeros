from __future__ import annotations
import os
import uuid
from careeros.config import GlobalConfig
from careeros.runtime.automation import AutomationRuntime
from careeros.runtime.claude_code import ApprovalCallback, ClaudeCodeRuntime
from careeros.runtime.local import LocalRuntime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.storage.interface import StorageProvider
from careeros.workspace.manager import open_workspace

WORKSPACE_ENV_VAR = "CAREEROS_WORKSPACE"


class WorkspaceNotConfigured(Exception):
    pass


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


def resolve_storage(workspace_path: str | None = None) -> LocalFilesystemStorage:
    """Find the workspace: explicit path, then CAREEROS_WORKSPACE, then config.

    The env-var tier is what an out-of-process agent needs — it exports the
    variable once and every subprocess it spawns finds the same workspace
    without a --workspace flag threaded through every call. Raises rather
    than printing, because a non-CLI caller has no terminal to print to.
    """
    if workspace_path:
        return LocalFilesystemStorage(workspace_path)
    env_path = os.environ.get(WORKSPACE_ENV_VAR)
    if env_path:
        return LocalFilesystemStorage(env_path)
    config = GlobalConfig.load()
    if not config.workspace_path:
        raise WorkspaceNotConfigured(
            "No workspace configured. Run 'careeros onboard' first."
        )
    return LocalFilesystemStorage(config.workspace_path)


def open_agent_runtime(
    *,
    workspace_path: str | None = None,
    approval_callback: ApprovalCallback,
    session_id: str | None = None,
) -> ClaudeCodeRuntime:
    """Open a workspace and a ClaudeCodeRuntime over it in one call.

    approval_callback stays required and keyword-only: construction must fail
    loudly rather than silently auto-denying every approval-gated action.
    """
    storage = resolve_storage(workspace_path)
    ctx = open_workspace(storage)
    return ClaudeCodeRuntime(
        storage, ctx, session_id or uuid.uuid4().hex, approval_callback
    )
