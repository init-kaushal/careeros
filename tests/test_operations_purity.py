import ast
from pathlib import Path

import pytest

OPERATIONS = Path(__file__).resolve().parent.parent / "careeros" / "operations"
FORBIDDEN = {"rich", "typer", "click"}


def _modules():
    return sorted(OPERATIONS.glob("*.py"))


def test_there_is_something_to_check():
    # Guards against this file silently passing because the glob went empty.
    assert len(_modules()) >= 2


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_presentation_library_imports(path):
    """The operations layer exists so a non-CLI caller can use it.

    A rich or typer import here means presentation leaked back in, which
    would make the layer unusable from an agent session — the exact failure
    this phase was built to fix. Asserted, not trusted.
    """
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & FORBIDDEN), path.name + " imports " + str(imported & FORBIDDEN)


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_print_calls(path):
    tree = ast.parse(path.read_text())
    called = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "print" not in called, path.name + " calls print()"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_sys_exit_calls(path):
    source = path.read_text()
    assert "sys.exit" not in source
    assert "SystemExit" not in source


def test_queue_only_denies_by_default_with_a_reason():
    from careeros.operations.approval_queue import queue_only
    from careeros.runtime.base import ActionProposal

    result = queue_only(ActionProposal(action="send_outreach", summary="Send?"))
    assert result.approved is False
    assert result.reason == "deferred to out-of-process approval"


def test_queue_only_is_accepted_as_a_claude_code_approval_callback(tmp_path):
    from careeros.operations.approval_queue import queue_only
    from careeros.runtime.base import ActionProposal
    from careeros.runtime.factory import open_claude_code_runtime
    from careeros.storage.filesystem import LocalFilesystemStorage
    from careeros.workspace.manager import init_workspace

    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    runtime = open_claude_code_runtime(storage, approval_callback=queue_only)
    result = runtime.request_approval(ActionProposal(action="send_outreach", summary="Send?"))
    assert result.approved is False
