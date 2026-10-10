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
