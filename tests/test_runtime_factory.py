import pytest
from unittest.mock import MagicMock
from careeros.runtime.factory import open_claude_code_runtime, open_local_runtime
from careeros.runtime.local import LocalRuntime
from careeros.runtime.claude_code import ClaudeCodeRuntime
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
