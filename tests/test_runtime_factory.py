import pytest
from unittest.mock import MagicMock, patch
from careeros.runtime.factory import open_claude_code_runtime, open_local_runtime, open_automation_runtime
from careeros.runtime.local import LocalRuntime
from careeros.runtime.claude_code import ClaudeCodeRuntime
from careeros.runtime.automation import AutomationRuntime
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_open_local_runtime_bootstraps_existing_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    runtime = open_local_runtime(storage)
    assert isinstance(runtime, LocalRuntime)
    assert runtime.session_id


def test_open_local_runtime_uses_provided_session_id(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    runtime = open_local_runtime(storage, session_id="fixed-id")
    assert runtime.session_id == "fixed-id"


def test_open_local_runtime_missing_manifest_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        open_local_runtime(storage)


def test_open_claude_code_runtime_bootstraps_existing_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    callback = MagicMock()
    runtime = open_claude_code_runtime(storage, approval_callback=callback)
    assert isinstance(runtime, ClaudeCodeRuntime)
    assert runtime.session_id


def test_open_claude_code_runtime_missing_manifest_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    callback = MagicMock()
    with pytest.raises(FileNotFoundError):
        open_claude_code_runtime(storage, approval_callback=callback)


def test_open_automation_runtime_bootstraps_existing_workspace(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    runtime = open_automation_runtime(storage)
    assert isinstance(runtime, AutomationRuntime)
    assert runtime.session_id


def test_open_automation_runtime_missing_manifest_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        open_automation_runtime(storage)


class TestResolveStorage:
    def test_explicit_path_wins_over_everything(self, tmp_path, monkeypatch):
        from careeros.runtime.factory import resolve_storage
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path / "from-env"))
        storage = resolve_storage(str(tmp_path / "explicit"))
        assert storage.resolve("manifest.json").startswith(str(tmp_path / "explicit"))

    def test_env_var_is_used_when_no_explicit_path(self, tmp_path, monkeypatch):
        from careeros.runtime.factory import resolve_storage
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))
        storage = resolve_storage()
        assert storage.resolve("manifest.json").startswith(str(tmp_path))

    def test_config_file_is_the_last_tier(self, tmp_path, monkeypatch):
        from careeros.config import GlobalConfig
        from careeros.runtime.factory import resolve_storage
        monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
        with patch.object(GlobalConfig, "load",
                          return_value=GlobalConfig(workspace_path=str(tmp_path))):
            storage = resolve_storage()
        assert storage.resolve("manifest.json").startswith(str(tmp_path))

    def test_raises_when_nothing_is_configured(self, monkeypatch):
        from careeros.config import GlobalConfig
        from careeros.runtime.factory import WorkspaceNotConfigured, resolve_storage
        monkeypatch.delenv("CAREEROS_WORKSPACE", raising=False)
        with patch.object(GlobalConfig, "load", return_value=GlobalConfig()):
            with pytest.raises(WorkspaceNotConfigured):
                resolve_storage()

    def test_an_empty_env_var_falls_through_to_config(self, tmp_path, monkeypatch):
        from careeros.config import GlobalConfig
        from careeros.runtime.factory import resolve_storage
        monkeypatch.setenv("CAREEROS_WORKSPACE", "")
        with patch.object(GlobalConfig, "load",
                          return_value=GlobalConfig(workspace_path=str(tmp_path))):
            storage = resolve_storage()
        assert storage.resolve("manifest.json").startswith(str(tmp_path))


class TestOpenAgentRuntime:
    def test_bootstraps_a_claude_code_runtime_from_the_env_var(self, tmp_path, monkeypatch):
        from careeros.operations.approval_queue import queue_only
        from careeros.runtime.claude_code import ClaudeCodeRuntime
        from careeros.runtime.factory import open_agent_runtime
        from careeros.storage.filesystem import LocalFilesystemStorage
        from careeros.workspace.manager import init_workspace

        init_workspace(LocalFilesystemStorage(str(tmp_path)))
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))

        runtime = open_agent_runtime(approval_callback=queue_only)
        assert isinstance(runtime, ClaudeCodeRuntime)
        assert runtime.agent_runtime_name == "claude_code"
        assert runtime.session_id

    def test_honors_an_explicit_session_id(self, tmp_path, monkeypatch):
        from careeros.operations.approval_queue import queue_only
        from careeros.runtime.factory import open_agent_runtime
        from careeros.storage.filesystem import LocalFilesystemStorage
        from careeros.workspace.manager import init_workspace

        init_workspace(LocalFilesystemStorage(str(tmp_path)))
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))
        runtime = open_agent_runtime(approval_callback=queue_only, session_id="agent-1")
        assert runtime.session_id == "agent-1"

    def test_missing_manifest_propagates_file_not_found(self, tmp_path, monkeypatch):
        from careeros.operations.approval_queue import queue_only
        from careeros.runtime.factory import open_agent_runtime
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))
        with pytest.raises(FileNotFoundError):
            open_agent_runtime(approval_callback=queue_only)

    def test_approval_callback_is_required_and_keyword_only(self, tmp_path, monkeypatch):
        from careeros.runtime.factory import open_agent_runtime
        monkeypatch.setenv("CAREEROS_WORKSPACE", str(tmp_path))
        with pytest.raises(TypeError):
            open_agent_runtime()
