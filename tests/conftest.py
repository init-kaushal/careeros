import pytest
from pathlib import Path


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Replace models.utc_now with a deterministic counter, one second per call."""
    state = {"n": 0}

    def tick() -> str:
        state["n"] += 1
        n = state["n"]
        return f"2026-10-06T09:{n // 60:02d}:{n % 60:02d}Z"

    monkeypatch.setattr("careeros.core.models.utc_now", tick)
    return tick


@pytest.fixture(scope="session")
def _career_template(tmp_path_factory: pytest.TempPathFactory):
    """Build the invented career memory once; each test gets its own copy."""
    from helpers import make_career, make_workspace

    root = make_workspace(tmp_path_factory.mktemp("career-template") / "ws")
    return root, make_career(root)


@pytest.fixture
def career(_career_template, tmp_path: Path, clock):
    """(workspace, ids by short name): a workspace holding a small invented career memory."""
    import shutil

    template, ids = _career_template
    root = tmp_path / "ws"
    shutil.copytree(template, root)
    return root, dict(ids)
