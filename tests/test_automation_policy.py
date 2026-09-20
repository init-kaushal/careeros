import pytest
from pydantic import ValidationError

from careeros.core.models import AutomationPolicy
from careeros.storage.filesystem import LocalFilesystemStorage
from careeros.workspace.manager import init_workspace


def test_save_and_load_round_trip(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    policy = AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["linkedin", "indeed"])
    policy.save(storage)
    loaded = AutomationPolicy.load(storage)
    assert loaded.auto_apply_min_score == 90
    assert loaded.max_auto_applies_per_run == 5
    assert loaded.boards == ["linkedin", "indeed"]


def test_load_missing_file_raises(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    init_workspace(storage)
    with pytest.raises(FileNotFoundError):
        AutomationPolicy.load(storage)


def test_rejects_negative_score_threshold():
    with pytest.raises(ValidationError):
        AutomationPolicy(auto_apply_min_score=-100, max_auto_applies_per_run=5, boards=["linkedin"])


def test_rejects_score_threshold_above_100():
    with pytest.raises(ValidationError):
        AutomationPolicy(auto_apply_min_score=101, max_auto_applies_per_run=5, boards=["linkedin"])


def test_rejects_absurd_max_auto_applies():
    with pytest.raises(ValidationError):
        AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=100000, boards=["linkedin"])


def test_rejects_zero_max_auto_applies():
    with pytest.raises(ValidationError):
        AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=0, boards=["linkedin"])


def test_rejects_unknown_board():
    with pytest.raises(ValidationError):
        AutomationPolicy(auto_apply_min_score=90, max_auto_applies_per_run=5, boards=["nope"])
