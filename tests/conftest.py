import pytest
from pathlib import Path


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"
